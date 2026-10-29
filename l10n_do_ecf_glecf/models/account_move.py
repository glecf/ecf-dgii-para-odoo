import base64
import logging
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import pytz
from psycopg2.errors import SerializationFailure

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .ecf_builder import DR_TZ, PURCHASE_TYPES, SUPPORTED_TYPES, build_ecf_data, ecf_type
from .glecf_client import GlecfError

_logger = logging.getLogger(__name__)

# GLECF document status -> state shown in Odoo.
WAITING = {"received", "validated", "queued", "processing", "signed", "submitted", "pending"}
FINAL = {
    "accepted": "accepted",
    "accepted_conditional": "conditional",
    "rejected": "rejected",
}
# GLECF retries a signing, sending or status error this many times by itself.
GLECF_RETRIES = 3
# Invoices in these states may already be at the DGII: never back to draft.
LOCKED_STATES = ("sent", "accepted", "conditional", "review")


class AccountMove(models.Model):
    _inherit = "account.move"

    glecf_state = fields.Selection(
        selection=[
            ("to_send", "Por enviar"),
            ("sent", "Esperando a la DGII"),
            ("accepted", "Aceptado"),
            ("conditional", "Aceptado con observaciones"),
            ("rejected", "Rechazado"),
            ("error", "No enviado"),
            ("review", "Requiere revisión"),
        ],
        string="Estado del e-CF",
        copy=False,
        readonly=True,
        tracking=True,
    )
    # Set before calling GLECF and kept: if the answer is lost (a timeout), the
    # document may exist in GLECF, so the invoice must not change anymore.
    glecf_attempted = fields.Boolean(copy=False, readonly=True)
    # Someone pressed «Enviar a la DGII»: send it even without «Enviar al publicar».
    glecf_requested = fields.Boolean(copy=False, readonly=True)
    # The status GLECF gave the document, to know what «Reintentar» can do.
    glecf_remote_status = fields.Char(copy=False, readonly=True)
    # It never reached the DGII (not signed, or the DGII refused the
    # certificate when GLECF identified itself): «Reintentar envío» is safe.
    glecf_retryable = fields.Boolean(copy=False, readonly=True)
    # Rejected, and the DGII did not say the e-NCF is spent: it can be
    # corrected and sent again with the same number.
    glecf_ncf_reusable = fields.Boolean(copy=False, readonly=True)
    # When GLECF took it: while the DGII answer is fresh, the job checks more often.
    glecf_sent_at = fields.Datetime(copy=False, readonly=True)
    # With «Send on posting» the invoice goes out on its own: the button would
    # only make people think they have to press it (Jorge, 2026-10-29).
    glecf_auto_send = fields.Boolean(related="company_id.glecf_auto_send")
    # A credit note of an invoice with ITBIS, with no tax on any line: Indexa
    # creates them so with «Porcentaje» or «Importe» (2026-10-29).
    glecf_refund_missing_tax = fields.Boolean(compute="_compute_glecf_refund_missing_tax")
    glecf_last_check = fields.Datetime(copy=False, readonly=True)
    glecf_document_id = fields.Char(string="Documento en GLECF", copy=False, readonly=True)
    glecf_message = fields.Text(string="Mensaje del e-CF", copy=False, readonly=True)
    # The DGII verification link GLECF built from the signed XML. The printed
    # QR uses it instead of rebuilding it here, so it always matches what the
    # DGII received (same signing time, same total).
    glecf_qr_url = fields.Char(string="Enlace de verificación de la DGII", copy=False, readonly=True)

    @api.depends("l10n_do_ecf_security_code", "l10n_do_ecf_sign_date", "invoice_date", "glecf_qr_url")
    def _compute_l10n_do_electronic_stamp(self):
        super()._compute_l10n_do_electronic_stamp()
        # l10n_do_accounting writes the signing time as stored by Odoo, in UTC,
        # four hours off the time in the XML: the DGII check would not match.
        # Fully escaped: the report decodes it once, and the QR must hold the
        # link exactly as GLECF wrote it (a bare space would cut it short).
        for move in self.filtered(lambda m: m.glecf_qr_url and m.state == "posted"):
            move.l10n_do_electronic_stamp = quote(move.glecf_qr_url, safe="")

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
            and self.is_ecf_invoice
            and not self.l10n_latam_manual_document_number
            and ecf_type(self) in SUPPORTED_TYPES
            and self.move_type in self._glecf_move_types()
        )

    def _glecf_move_types(self):
        """Sales go out on customer invoices and notes; purchases, minor
        expenses and payments abroad on vendor bills."""
        if ecf_type(self) in PURCHASE_TYPES:
            return ("in_invoice",)
        return ("out_invoice", "out_refund")

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
                self._glecf_trigger_sync()
        return posted

    def button_draft(self):
        for move in self:
            if move.glecf_state in LOCKED_STATES or (move.glecf_state == "to_send" and move.glecf_attempted):
                raise UserError(
                    _("%s ya se envió a GLECF y puede estar en la DGII: no se puede cambiar. "
                      "Si hace falta corregirla, emite una nota de crédito o de débito.") % move.name
                )
        # Back in draft, nothing must go out until it is posted again.
        self.filtered(lambda m: m.glecf_state in ("to_send", "error")).write({
            "glecf_state": False, "glecf_requested": False,
        })
        return super().button_draft()

    # -- Sending --------------------------------------------------------------

    def action_glecf_send(self):
        """«Enviar a la DGII»: queue them and let the job send them.

        Sending here, in the user's request, would undo the invoices already
        delivered to GLECF if a later one failed, and a long list could run
        past the time Odoo gives a request.
        """
        moves = self.filtered(lambda m: m.glecf_state in ("to_send", "error") and m._glecf_applies())
        for company in moves.company_id:
            company._glecf_check_ready()
        moves.write({"glecf_state": "to_send", "glecf_requested": True, "glecf_message": False})
        if moves:
            self._glecf_trigger_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "message": _("%s e-CF se enviarán a la DGII en un momento.") % len(moves)
                if moves else _("No hay e-CF por enviar en lo seleccionado."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_glecf_retry(self):
        """Send again a document GLECF could not sign (e.g. no certificate yet)."""
        for move in self.filtered(lambda m: m.glecf_state == "review" and m.glecf_retryable):
            move.company_id._glecf_check_ready()
            move.write({
                "glecf_state": "to_send", "glecf_requested": True, "glecf_message": False,
                "glecf_retryable": False,
            })
        self._glecf_trigger_sync()
        return True

    def action_glecf_correct(self):
        """«Corregir la factura»: back to draft a rejected e-CF whose number
        can be used again. The Dominican localization only lets cancelled
        invoices go back to draft, and cancelling would void the number."""
        moves = self.filtered(
            lambda m: m.state == "posted" and m.glecf_state == "rejected" and m.glecf_ncf_reusable
        )
        moves.write({"glecf_ncf_reusable": False})
        return moves.button_draft()

    def action_glecf_refresh(self):
        for move in self.filtered(lambda m: m.glecf_state == "sent"):
            move._glecf_refresh()
        return True

    def _glecf_idempotency_key(self):
        # The e-NCF goes in the key: a copy of this database (with the same
        # uuid) or a new number on the same invoice never takes the document
        # of another invoice.
        db_uuid = self.env["ir.config_parameter"].sudo().get_param("database.uuid", "")
        return "odoo-%s-%s-%s" % (db_uuid.replace("-", "")[:12], self.id, self.l10n_do_fiscal_number)

    def _glecf_send(self):
        self.ensure_one()
        if not self._glecf_applies():
            # Back in draft or cancelled since it was queued.
            self.write({"glecf_state": False, "glecf_requested": False})
            return
        mismatch = self.company_id._glecf_rnc_mismatch_message() or self.company_id._glecf_not_issuer_message()
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
        self.write({"glecf_attempted": True})
        # Kept before calling GLECF: if this write clashes with someone saving
        # the invoice, it fails here, before anything is sent. Seen on
        # 2026-10-29: the send ran while the posting was still being saved,
        # GLECF got the e-CF and the invoice stayed «to send», unlocked.
        self.env.cr.commit()
        try:
            answer = self.company_id._glecf_client().submit(payload)
        except GlecfError as error:
            if error.transient:
                # GLECF did not answer: maybe it never got it, maybe it did.
                # The same key makes the next try safe either way.
                self._glecf_note(_("GLECF no respondió (%s). Se reintentará solo en unos minutos.") % error.message)
                return
            self._glecf_fail(error.message)
            return

        self.write({
            "glecf_document_id": answer.get("id"),
            "glecf_sent_at": fields.Datetime.now(),
            "glecf_state": "sent",
            "glecf_attempted": False,
            "glecf_requested": False,
            "glecf_message": False,
        })
        if (answer.get("status") or "").lower() in WAITING:
            self.message_post(body=_("e-CF enviado a GLECF. Esperando la respuesta de la DGII."))
        # The answer to a submission is short (no security code, QR or XML,
        # and no error detail): read the whole document.
        self._glecf_refresh()

    def _glecf_fail(self, message):
        # A clear answer: whatever GLECF has, it is not a lost document.
        self.write({
            "glecf_state": "error", "glecf_message": message,
            "glecf_attempted": False, "glecf_requested": False,
        })
        self.message_post(body=_("El e-CF no se envió: %s") % message)

    def _glecf_note(self, message):
        """Show a message on the invoice, once, without changing its state."""
        if self.glecf_message != message:
            self.write({"glecf_message": message})
            self.message_post(body=message)

    # -- Reading the answer ---------------------------------------------------

    def _glecf_refresh(self):
        self.ensure_one()
        if not self.glecf_document_id:
            return
        self.write({"glecf_last_check": fields.Datetime.now()})
        try:
            document = self.company_id._glecf_client().get_document(self.glecf_document_id)
        except GlecfError as error:
            _logger.info("GLECF status of %s not available: %s", self.name, error.message)
            return
        self._glecf_apply(document)

    def _glecf_apply(self, document):
        """Update the invoice with what GLECF says about its document."""
        status = (document.get("status") or "").lower()
        self.write({"glecf_remote_status": status})
        encf = document.get("encf")
        if encf and self.l10n_do_fiscal_number and encf != self.l10n_do_fiscal_number:
            self._glecf_review(
                _("GLECF devolvió el comprobante %(theirs)s, no el %(ours)s de esta factura. "
                  "No se ha tocado nada: escribe a soporte@glecf.com.")
                % {"theirs": encf, "ours": self.l10n_do_fiscal_number}
            )
            return
        if status in WAITING:
            return
        if status.startswith("error_"):
            self._glecf_apply_error(document, status)
            return
        state = FINAL.get(status)
        if not state:
            return

        self.company_id._glecf_set_environment(document.get("environment"))
        values = {
            "glecf_state": state,
            "glecf_message": document.get("dgii_response_message") or False,
            "glecf_attempted": False,
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
            reason = values["glecf_message"] or "-"
            if str(document.get("dgii_response_code") or "") == "145":
                reason += _(". La fecha de vencimiento de la secuencia no es la que autorizó la DGII: "
                            "corrígela en Ajustes > Facturación > Configurar las secuencias e-NCF")
            # The DGII says whether a rejected e-NCF is spent; usually it is.
            used = document.get("secuencia_utilizada")
            if used is True:
                body = _("La DGII rechazó el e-CF: %s. Ese e-NCF ya quedó usado y no se puede volver "
                         "a enviar: cancela esta factura y duplícala para emitirla con otro número.") % reason
            elif used is False:
                body = _("La DGII rechazó el e-CF: %s. El e-NCF se puede volver a usar: pulsa «Corregir "
                         "la factura», corrige lo que haga falta y confírmala otra vez.") % reason
            else:
                body = _("La DGII rechazó el e-CF: %s. Pulsa «Corregir la factura», corrige lo que haga "
                         "falta y confírmala otra vez: se reenviará con el mismo e-NCF. Si la DGII responde "
                         "que esa secuencia ya se usó, cancela esta factura y créala de nuevo para que tome "
                         "otro número.") % reason
            self.write({"glecf_message": body, "glecf_ncf_reusable": used is not True})
        elif state == "conditional":
            body = _("La DGII aceptó el e-CF con observaciones: %s") % (values["glecf_message"] or "-")
        else:
            body = _("La DGII aceptó el e-CF.")
        self.message_post(body=body)

    def _glecf_apply_error(self, document, status):
        message = document.get("last_error") or document.get("message") or status
        if status == "error_validation":
            # Bad data: it never reached the DGII. Correct and post again.
            self._glecf_fail(message)
        elif status == "error_signature":
            # Never reached the DGII either; usually there is no certificate yet.
            self._glecf_review(
                _("GLECF no pudo firmar el e-CF: %s. Revisa el certificado digital en Ajustes "
                  "y pulsa «Reintentar envío».") % message,
                retryable=True,
            )
        elif document.get("reintentable"):
            # The DGII refused the certificate when GLECF identified itself:
            # nothing was sent, so trying again is safe once it is fixed.
            self._glecf_review(self._glecf_certificate_refused_text(message), retryable=True)
        elif (document.get("retry_count") or 0) >= GLECF_RETRIES:
            # Sending or the DGII answer failed and GLECF stopped retrying. It
            # may be at the DGII: nobody resends it from here.
            self._glecf_review(
                _("GLECF no pudo confirmar este e-CF con la DGII: %s. Escribe a soporte@glecf.com "
                  "para revisarlo; no lo vuelvas a emitir.") % message
            )
        else:
            self._glecf_note(_("GLECF lo está reintentando: %s") % message)

    @staticmethod
    def _glecf_certificate_refused_text(message):
        """What to do when the DGII refuses the certificate, without the jargon."""
        quoted = re.search(r"[\"«]([^\"»]+)[\"»]", message or "")
        dgii = quoted.group(1).strip() if quoted else (message or "")
        if "delegad" in dgii.lower():
            return _(
                "La DGII no aceptó el certificado: la persona del certificado todavía no puede firmar "
                "por la empresa («%s»). El administrador de e-CF de la empresa debe darle el rol de "
                "Firmante en la Oficina Virtual de la DGII (Delegación de e-CF > Delegación de Roles). "
                "El comprobante no llegó a la DGII: cuando esté hecho, pulsa «Reintentar envío»."
            ) % dgii
        return _(
            "La DGII no aceptó el certificado: «%s». El comprobante no llegó a la DGII: revisa el "
            "certificado y pulsa «Reintentar envío»."
        ) % dgii

    def _glecf_review(self, message, retryable=False):
        self.write({
            "glecf_state": "review", "glecf_message": message, "glecf_requested": False,
            "glecf_retryable": retryable,
        })
        self.message_post(body=message)

    def _glecf_notify_change(self):
        """Tell the screens showing this invoice that its e-CF status changed,
        so they reload on their own (static/src/js/glecf_refresh.js)."""
        users = self.create_uid | self.invoice_user_id | self.write_uid
        for partner in users.filtered(lambda u: not u.share).partner_id:
            self.env["bus.bus"]._sendone(partner, "glecf_move_updated", {"ids": self.ids})

    @api.depends("move_type", "reversed_entry_id", "invoice_line_ids.tax_ids")
    def _compute_glecf_refund_missing_tax(self):
        for move in self:
            origin = move.reversed_entry_id
            lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
            move.glecf_refund_missing_tax = bool(
                move.move_type in ("out_refund", "in_refund")
                and origin and origin.amount_tax
                and lines and not lines.tax_ids
            )

    def action_glecf_add_origin_tax(self):
        """Put the tax of the original invoice on the credit note lines with none."""
        for move in self.filtered(lambda m: m.state == "draft" and m.glecf_refund_missing_tax):
            origin_lines = move.reversed_entry_id.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
            )
            tax_sets = {line.tax_ids for line in origin_lines}
            if len(tax_sets) != 1:
                raise UserError(_(
                    "La factura original lleva impuestos distintos en sus líneas: pon a cada línea de "
                    "la nota de crédito el que le corresponda."
                ))
            taxes = tax_sets.pop()
            move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product" and not line.tax_ids
            ).write({"tax_ids": [(6, 0, taxes.ids)]})
        return True

    # -- Background job -------------------------------------------------------

    def _glecf_trigger_sync(self):
        """Run the job now. If it clashes with the invoice being saved, the
        «attempted» write fails before anything is sent and the invoice goes
        on the next pass (see _glecf_send). A delay did not help: Odoo's job
        runner only wakes up once a minute for a job due later (2026-10-29)."""
        self.env.ref("l10n_do_ecf_glecf.ir_cron_glecf_sync")._trigger()

    @api.model
    def _cron_glecf_sync(self, batch=50):
        """Send what is waiting and read the answers of what was sent.

        Each invoice is committed on its own, so a failure never undoes the
        invoices sent before it, and one that breaks never stops the rest.
        Searches run as superuser: the job serves every company.
        """
        moves = self.sudo()
        to_send = [
            ("state", "=", "posted"),
            ("glecf_state", "=", "to_send"),
            ("company_id.glecf_consent", "=", True),
            ("company_id.glecf_api_key", "!=", False),
            "|", ("glecf_requested", "=", True), ("company_id.glecf_auto_send", "=", True),
        ]
        pending = moves.search(to_send, order="id", limit=batch)
        waiting = moves.search(
            [("glecf_state", "=", "sent")], order="glecf_last_check asc nulls first, id", limit=batch * 2
        )
        for move, step, wanted in (
            [(m, "_glecf_send", "to_send") for m in pending]
            + [(m, "_glecf_refresh", "sent") for m in waiting]
        ):
            # Up to three tries: right after posting, the invoice is often
            # still being saved and the first write clashes. Trying again is
            # safe: the «attempted» write comes before calling GLECF, and a
            # second call with the same key gets back the same document.
            for attempt in range(3):
                try:
                    # A fresh look at the invoice: it may have changed since the
                    # search (just posted, sent by another worker, back to draft).
                    self.env.cr.commit()
                    move.invalidate_recordset()
                    if move.glecf_state != wanted:
                        break
                    getattr(move.with_company(move.company_id), step)()
                    if move.glecf_state != wanted:
                        move._glecf_notify_change()
                    self.env.cr.commit()
                    break
                except SerializationFailure:
                    self.env.cr.rollback()
                    time.sleep(2)
                except Exception:
                    self.env.cr.rollback()
                    _logger.exception("GLECF: %s failed for %s", step, move.display_name)
                    break
        # What is left of the plan changes with every e-CF sent: read it again
        # for the companies that sent something, and once a day for every
        # connected company (a company that is not an issuer today may be one
        # tomorrow).
        day_ago = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        stale = self.env["res.company"].sudo().search([
            ("glecf_api_key", "!=", False),
            "|", ("glecf_account_checked", "=", False), ("glecf_account_checked", "<", day_ago),
        ])
        # The e-CF the companies received, as draft vendor bills.
        receiving = self.env["res.company"].sudo().search([
            ("glecf_api_key", "!=", False), ("glecf_receive", "=", True),
        ])
        for company in receiving:
            try:
                company._glecf_fetch_received()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("GLECF: received e-CF of %s not brought", company.name)
        for company in pending.company_id | stale:
            try:
                company._glecf_refresh_account()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.info("GLECF: plan of %s not available", company.name)
        # The DGII usually answers within a minute or two: while something sent
        # in the last quarter of an hour still waits, look again in 30 seconds
        # instead of the usual five minutes. Older ones wait for the usual run.
        fresh = fields.Datetime.subtract(fields.Datetime.now(), minutes=15)
        # Also what is still waiting to go out (it clashed with someone saving
        # it): only what this job would send, or it would loop forever.
        if moves.search_count(to_send) or moves.search_count(
            [("glecf_state", "=", "sent"), ("glecf_sent_at", ">=", fresh)]
        ):
            self.env.ref("l10n_do_ecf_glecf.ir_cron_glecf_sync")._trigger(
                at=fields.Datetime.now() + timedelta(seconds=30)
            )


def _parse_sign_date(text):
    """'dd-mm-yyyy HH:MM:SS' in Dominican time -> naive UTC for Odoo."""
    if not text:
        return False
    try:
        local = datetime.strptime(text.strip(), "%d-%m-%Y %H:%M:%S")
    except ValueError:
        return False
    return DR_TZ.localize(local).astimezone(pytz.utc).replace(tzinfo=None)
