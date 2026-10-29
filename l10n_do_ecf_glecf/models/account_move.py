import base64
import logging
from datetime import datetime
from urllib.parse import quote_plus

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ecf_builder import DR_TZ, SUPPORTED_TYPES, build_ecf_data, ecf_type
from .glecf_client import GlecfError

_logger = logging.getLogger(__name__)

# GLECF document status -> state shown in Odoo.
WAITING = {"received", "validated", "queued", "processing", "signed", "submitted", "pending"}
FINAL = {
    "accepted": "accepted",
    "accepted_conditional": "conditional",
    "rejected": "rejected",
}
# Invoices in these states already reached GLECF: they cannot go back to draft.
SENT_STATES = ("sent", "accepted", "conditional")


class AccountMove(models.Model):
    _inherit = "account.move"

    glecf_state = fields.Selection(
        selection=[
            ("to_send", "To send"),
            ("sent", "Waiting for the DGII"),
            ("accepted", "Accepted"),
            ("conditional", "Accepted with observations"),
            ("rejected", "Rejected"),
            ("error", "Not sent"),
        ],
        string="e-CF status",
        copy=False,
        readonly=True,
        tracking=True,
    )
    glecf_document_id = fields.Char(string="GLECF document", copy=False, readonly=True)
    glecf_message = fields.Text(string="e-CF message", copy=False, readonly=True)
    # The DGII verification link GLECF built from the signed XML. The printed
    # QR uses it instead of rebuilding it here, so it always matches what the
    # DGII received (same signing time, same total).
    glecf_qr_url = fields.Char(string="DGII verification link", copy=False, readonly=True)

    @api.depends("l10n_do_ecf_security_code", "l10n_do_ecf_sign_date", "invoice_date", "glecf_qr_url")
    def _compute_l10n_do_electronic_stamp(self):
        super()._compute_l10n_do_electronic_stamp()
        # l10n_do_accounting writes the signing time as stored by Odoo, in UTC,
        # four hours off the time in the XML: the DGII check would not match.
        for move in self.filtered(lambda m: m.glecf_qr_url and m.state == "posted"):
            move.l10n_do_electronic_stamp = quote_plus(move.glecf_qr_url, safe="%")

    def _glecf_sign_date_text(self):
        """Signing time as the DGII wants it printed: Dominican time, dd-mm-yyyy HH:MM:SS."""
        self.ensure_one()
        if not self.l10n_do_ecf_sign_date:
            return ""
        signed = pytz.utc.localize(self.l10n_do_ecf_sign_date).astimezone(DR_TZ)
        return signed.strftime("%d-%m-%Y %H:%M:%S")

    # -- Which invoices -------------------------------------------------------

    def _glecf_applies(self):
        self.ensure_one()
        return (
            self.state == "posted"
            and self.move_type in ("out_invoice", "out_refund")
            and self.is_ecf_invoice
            and not self.l10n_latam_manual_document_number
            and ecf_type(self) in SUPPORTED_TYPES
        )

    def _post(self, soft=True):
        posted = super()._post(soft)
        # An invoice that was not sent or was rejected, corrected and posted
        # again, goes out again with the same e-NCF: GLECF reprocesses its
        # document because the idempotency key is the same.
        to_send = posted.filtered(
            lambda move: move.glecf_state in (False, "error", "rejected") and move._glecf_applies()
        )
        if to_send:
            to_send.write({"glecf_state": "to_send", "glecf_message": False})
            # Never send inside the posting transaction: if it were rolled
            # back, the DGII would have a document Odoo does not. The job
            # runs right after the commit.
            if any(to_send.company_id.mapped("glecf_auto_send")):
                self.env.ref("l10n_do_ecf_glecf.ir_cron_glecf_sync")._trigger()
        return posted

    def button_draft(self):
        for move in self:
            if move.glecf_state in SENT_STATES:
                raise UserError(
                    _("%s was already sent to the DGII and cannot be changed. "
                      "Issue a credit or debit note instead.") % move.name
                )
        return super().button_draft()

    # -- Sending --------------------------------------------------------------

    def action_glecf_send(self):
        for move in self:
            if move.glecf_state not in ("to_send", "error"):
                continue
            move.company_id._glecf_check_ready()
            move._glecf_send()
        return True

    def action_glecf_refresh(self):
        for move in self.filtered(lambda m: m.glecf_state == "sent"):
            move._glecf_refresh()
        return True

    def _glecf_idempotency_key(self):
        db_uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid", "")
        return "odoo-%s-%s" % (db_uuid.replace("-", "")[:12], self.id)

    def _glecf_send(self):
        self.ensure_one()
        mismatch = self.company_id._glecf_rnc_mismatch_message()
        if mismatch:
            self._glecf_fail(mismatch)
            return
        try:
            ecf_data = build_ecf_data(self)
        except UserError as error:
            self._glecf_fail(error.args[0])
            return
        payload = {
            "idempotency_key": self._glecf_idempotency_key(),
            "ecf_data": ecf_data,
        }
        try:
            answer = self.company_id._glecf_client().submit(payload)
        except GlecfError as error:
            self._glecf_fail(error.message)
            return

        self.write({
            "glecf_document_id": answer.get("id"),
            "glecf_state": "sent",
            "glecf_message": False,
        })
        if (answer.get("status") or "").lower() in WAITING:
            self.message_post(body=_("e-CF sent to GLECF. Waiting for the answer of the DGII."))
        self._glecf_apply(answer)

    def _glecf_fail(self, message):
        self.write({"glecf_state": "error", "glecf_message": message})
        self.message_post(body=_("The e-CF was not sent: %s") % message)

    # -- Reading the answer ---------------------------------------------------

    def _glecf_refresh(self):
        self.ensure_one()
        if not self.glecf_document_id:
            return
        try:
            document = self.company_id._glecf_client().get_document(self.glecf_document_id)
        except GlecfError as error:
            _logger.info("GLECF status of %s not available: %s", self.name, error.message)
            return
        self._glecf_apply(document)

    def _glecf_apply(self, document):
        """Update the invoice with what GLECF says about its document."""
        status = (document.get("status") or "").lower()
        if status in WAITING:
            return
        if status.startswith("error_"):
            message = document.get("last_error") or document.get("message") or status
            # A submission error is retried by GLECF itself; a validation
            # error means the data must be corrected.
            if status == "error_validation":
                self._glecf_fail(message)
            return
        state = FINAL.get(status)
        if not state:
            return

        self.company_id._glecf_set_environment(document.get("environment"))
        values = {
            "glecf_state": state,
            "glecf_message": document.get("dgii_response_message") or False,
        }
        if document.get("security_code"):
            values["l10n_do_ecf_security_code"] = document["security_code"]
        if document.get("qr_url"):
            values["glecf_qr_url"] = document["qr_url"]
        sign_date = _parse_sign_date(document.get("signature_date"))
        if sign_date:
            values["l10n_do_ecf_sign_date"] = sign_date
        if document.get("xml_signed"):
            values["l10n_do_ecf_edi_file"] = base64.b64encode(document["xml_signed"].encode())
            values["l10n_do_ecf_edi_file_name"] = "%s.xml" % self.l10n_do_fiscal_number
        self.write(values)

        if state == "rejected":
            body = _("The DGII rejected the e-CF: %s") % (values["glecf_message"] or "-")
        elif state == "conditional":
            body = _("The DGII accepted the e-CF with observations: %s") % (values["glecf_message"] or "-")
        else:
            body = _("The DGII accepted the e-CF.")
        self.message_post(body=body)

    # -- Background job -------------------------------------------------------

    @api.model
    def _cron_glecf_sync(self, batch=50):
        """Send what is waiting and read the answers of what was sent.

        Each invoice is committed on its own, so a failure never undoes the
        invoices sent before it.
        """
        pending = self.search([
            ("glecf_state", "=", "to_send"),
            ("company_id.glecf_auto_send", "=", True),
            ("company_id.glecf_consent", "=", True),
        ], limit=batch)
        for move in pending:
            if not move.company_id.sudo().glecf_api_key:
                continue
            move._glecf_send()
            self.env.cr.commit()

        waiting = self.search([("glecf_state", "=", "sent")], order="write_date asc", limit=batch * 2)
        for move in waiting:
            move._glecf_refresh()
            self.env.cr.commit()


def _parse_sign_date(text):
    """'dd-mm-yyyy HH:MM:SS' in Dominican time -> naive UTC for Odoo."""
    if not text:
        return False
    try:
        local = datetime.strptime(text.strip(), "%d-%m-%Y %H:%M:%S")
    except ValueError:
        return False
    return DR_TZ.localize(local).astimezone(pytz.utc).replace(tzinfo=None)
