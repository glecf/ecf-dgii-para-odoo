"""Bring the e-CF the company received into Odoo as vendor bills.

GLECF receives them at its own address (the one registered at the DGII),
answers the acknowledgement to the supplier and keeps them. A company that
works in Odoo does its 606 there, so each received e-CF becomes a vendor bill
**in draft**: a person checks it and posts it. Nothing is posted here.

Rules, from what Odoo does in other countries and what the DGII asks:

- Never twice: the GLECF id, and the supplier RNC with the e-NCF, are unique
  per company.
- Never lost: if the XML cannot be read, the draft is created anyway with the
  XML attached and the reason in the chatter.
- The supplier is found by RNC; if missing, it is created from the XML.
- Credit (E34) and debit (E33) notes are linked to the bill they modify when
  it is in Odoo; if not, the NCF they cite is kept and the chatter says so,
  without guessing a link.
"""

import base64
import logging
from datetime import datetime

from lxml import etree

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# e-CF types a company receives from a supplier, and what they become.
BILL_TYPES = ("31", "32", "44", "45")
CREDIT_NOTE = "34"
DEBIT_NOTE = "33"
RECEIVED_TYPES = BILL_TYPES + (CREDIT_NOTE, DEBIT_NOTE)

# DGII IndicadorFacturacion -> purchase tax of the Dominican chart.
TAX_BY_INDICATOR = {"1": "tax_18_purch", "2": "tax_16_purch", "3": "tax_0_purch"}


def _text(node, path):
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _number(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _date(text):
    try:
        return datetime.strptime(text, "%d-%m-%Y").date()
    except (TypeError, ValueError):
        return False


def _digits(text):
    return "".join(char for char in (text or "") if char.isdigit())


def read_ecf(xml_text):
    """What the vendor bill needs from the e-CF XML. Raises ValueError."""
    root = etree.fromstring(xml_text.encode("utf-8"))
    header = root.find("Encabezado")
    if header is None:
        raise ValueError("el XML no tiene Encabezado")
    lines = []
    for item in root.findall("DetallesItems/Item"):
        quantity = _number(_text(item, "CantidadItem"), 1.0) or 1.0
        amount = _number(_text(item, "MontoItem"))
        lines.append({
            "name": _text(item, "NombreItem") or "-",
            "description": _text(item, "DescripcionItem"),
            "quantity": quantity,
            # MontoItem is the net amount of the line (after its discount).
            "price_unit": amount / quantity,
            "indicator": _text(item, "IndicadorFacturacion"),
        })
    return {
        "type": _text(header, "IdDoc/TipoeCF"),
        "encf": _text(header, "IdDoc/eNCF"),
        "issue_date": _date(_text(header, "Emisor/FechaEmision")),
        "due_date": _date(_text(header, "IdDoc/FechaLimitePago")),
        "supplier_rnc": _digits(_text(header, "Emisor/RNCEmisor")),
        "supplier_name": _text(header, "Emisor/RazonSocialEmisor"),
        "buyer_rnc": _digits(_text(header, "Comprador/RNCComprador")),
        "total": _number(_text(header, "Totales/MontoTotal")),
        "modified_ncf": _text(root, "InformacionReferencia/NCFModificado"),
        "modification_code": _text(root, "InformacionReferencia/CodigoModificacion"),
        "lines": lines,
    }


class ResCompany(models.Model):
    _inherit = "res.company"

    glecf_receive = fields.Boolean(
        string="Traer los e-CF recibidos",
        default=True,
        help="Los e-CF que te mandan tus proveedores llegan a GLECF y entran en Odoo como "
        "facturas de proveedor en borrador.",
    )
    glecf_received_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de los recibidos",
        domain="[('type', '=', 'purchase'), ('company_id', '=', id)]",
        help="Diario de compras donde entran las facturas de proveedor recibidas.",
    )
    glecf_received_cursor = fields.Char(readonly=True, copy=False)

    def action_glecf_fetch_received(self):
        """Ask the background job to bring them now: done here, in the request,
        a long list could run past the time Odoo gives a page."""
        self.ensure_one()
        self._glecf_check_admin()
        self.env.ref("l10n_do_ecf_glecf.ir_cron_glecf_sync")._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "message": _("Los e-CF recibidos nuevos entrarán como borradores en un momento."),
            },
        }

    def _glecf_received_journal(self):
        return self.glecf_received_journal_id or self.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", self.id)], limit=1
        )

    def _glecf_fetch_received(self, max_pages=10):
        """Bring the new received e-CF. Commits each one: a failure never loses
        the ones already brought, and the cursor only moves past what is saved."""
        self.ensure_one()
        client = self._glecf_client()
        moves = self.env["account.move"].with_company(self).sudo()
        count = 0
        for _page in range(max_pages):
            answer = client.received_feed(after=self.glecf_received_cursor, limit=50)
            for item in answer.get("items", []):
                if moves._glecf_import_received(self, item):
                    count += 1
                self.sudo().glecf_received_cursor = "%s|%s" % (item["created_at"], item["id"])
                self.env.cr.commit()
            if not answer.get("has_more"):
                break
        return count


class AccountMove(models.Model):
    _inherit = "account.move"

    glecf_received_id = fields.Char(string="Recibido en GLECF", copy=False, readonly=True, index=True)
    glecf_approval_state = fields.Selection(
        selection=[("aceptado", "Aceptada"), ("rechazado", "Rechazada")],
        string="Aprobación comercial",
        copy=False,
        readonly=True,
        tracking=True,
    )
    glecf_approval_message = fields.Char(string="Aprobación al proveedor", copy=False, readonly=True)
    # What happened with the copy for the supplier: entregado | fallido |
    # sin_direccion | no_aplica. Only a failed one can be sent again.
    glecf_approval_supplier = fields.Char(copy=False, readonly=True)

    _sql_constraints = [
        ("glecf_received_unique", "unique(company_id, glecf_received_id)",
         "Este e-CF recibido ya está en Odoo."),
    ]

    # -- Import -----------------------------------------------------------------

    def _glecf_import_received(self, company, item):
        """Create the draft for one received e-CF. True if a new one was made."""
        if item.get("ncf_type") not in RECEIVED_TYPES:
            return False
        if self.search_count([("company_id", "=", company.id), ("glecf_received_id", "=", item["id"])]):
            return False
        supplier_rnc = _digits(item.get("rnc_emisor"))
        if supplier_rnc and item.get("encf") and self.search_count([
            ("company_id", "=", company.id),
            ("move_type", "in", ("in_invoice", "in_refund")),
            ("l10n_do_fiscal_number", "=", item["encf"]),
            ("partner_id.vat", "=", supplier_rnc),
        ]):
            # Already typed in by hand: keep that one.
            return False

        try:
            data = read_ecf(item.get("raw_xml") or "")
        except (ValueError, etree.XMLSyntaxError) as error:
            return self._glecf_unreadable_received(company, item, str(error))

        if data["buyer_rnc"] and data["buyer_rnc"] != _digits(company.vat):
            _logger.warning("GLECF: received %s is for RNC %s, not %s", item["encf"],
                            data["buyer_rnc"], company.vat)
            return False

        partner = self._glecf_supplier(company, data["supplier_rnc"] or supplier_rnc,
                                       data["supplier_name"] or item.get("razon_social_emisor"))
        tipo = data["type"] or item["ncf_type"]
        move_type = "in_refund" if tipo == CREDIT_NOTE else "in_invoice"
        document_type = self.env["l10n_latam.document.type"].search(
            [("doc_code_prefix", "=", "E" + tipo)], limit=1
        )
        journal = company._glecf_received_journal()
        values = {
            "move_type": move_type,
            "company_id": company.id,
            "journal_id": journal.id,
            "partner_id": partner.id,
            "invoice_date": data["issue_date"] or fields.Date.today(),
            "glecf_received_id": item["id"],
            "invoice_line_ids": [(0, 0, self._glecf_received_line(company, line)) for line in data["lines"]],
        }
        if data["due_date"]:
            values["invoice_date_due"] = data["due_date"]
        if document_type:
            values["l10n_latam_document_type_id"] = document_type.id
        if partner.l10n_do_expense_type:
            values["l10n_do_expense_type"] = partner.l10n_do_expense_type
        origin = self.browse()
        if data["modified_ncf"]:
            values["l10n_do_origin_ncf"] = data["modified_ncf"]
            origin = self.search([
                ("company_id", "=", company.id),
                ("partner_id", "=", partner.id),
                ("move_type", "=", "in_invoice"),
                ("l10n_do_fiscal_number", "=", data["modified_ncf"]),
            ], limit=1)
            if origin and tipo == CREDIT_NOTE:
                values["reversed_entry_id"] = origin.id
            elif origin and tipo == DEBIT_NOTE and "debit_origin_id" in self._fields:
                values["debit_origin_id"] = origin.id

        move = self.create(values)
        # The supplier's e-NCF is the number of the vendor bill: the 606 reads it.
        move.l10n_latam_document_number = data["encf"] or item["encf"]
        self._glecf_attach_xml(move, item)

        notes = [_("e-CF recibido en GLECF de %(name)s (RNC %(rnc)s), traído como borrador.") % {
            "name": partner.name, "rnc": partner.vat or "-"}]
        if data["modified_ncf"] and not origin:
            notes.append(_("Modifica el %s, que no está en Odoo: enlázalo a mano si lo tienes.")
                         % data["modified_ncf"])
        if abs(move.amount_total - data["total"]) > 0.01:
            notes.append(_("El total del e-CF es %(ecf).2f y el de este borrador %(odoo).2f: revisa "
                           "los impuestos de las líneas antes de publicarlo.")
                         % {"ecf": data["total"], "odoo": move.amount_total})
        if not move.l10n_do_expense_type:
            notes.append(_("Indica el tipo de gasto para el 606 antes de publicarla."))
        move.message_post(body="<br/>".join(notes))
        return True

    def _glecf_unreadable_received(self, company, item, reason):
        """Never lose a received e-CF: a draft with its XML and the reason."""
        move = self.create({
            "move_type": "in_invoice",
            "company_id": company.id,
            "journal_id": company._glecf_received_journal().id,
            "glecf_received_id": item["id"],
            "ref": item.get("encf"),
        })
        self._glecf_attach_xml(move, item)
        move.message_post(body=_("No se pudo leer el e-CF %(encf)s recibido en GLECF (%(reason)s). "
                                 "Está adjunto: complétalo a mano.")
                          % {"encf": item.get("encf") or "-", "reason": reason})
        return True

    def _glecf_supplier(self, company, rnc, name):
        partners = self.env["res.partner"].with_company(company)
        partner = partners.search([("vat", "=", rnc), ("company_id", "in", (company.id, False))], limit=1)
        if not partner and rnc:
            partner = partners.search([("vat", "ilike", rnc)], limit=1)
        if partner:
            return partner.commercial_partner_id
        return partners.create({
            "name": name or rnc or _("Proveedor sin nombre"),
            "vat": rnc,
            "is_company": True,
            "country_id": self.env.ref("base.do").id,
            "company_id": company.id,
        })

    def _glecf_received_line(self, company, line):
        tax_ref = TAX_BY_INDICATOR.get(line["indicator"])
        tax = self.env.ref("account.%s_%s" % (company.id, tax_ref), raise_if_not_found=False) if tax_ref else None
        name = line["name"] if not line["description"] else "%s\n%s" % (line["name"], line["description"])
        return {
            "name": name,
            "quantity": line["quantity"],
            "price_unit": line["price_unit"],
            "tax_ids": [(6, 0, tax.ids if tax else [])],
        }

    def _glecf_attach_xml(self, move, item):
        xml = item.get("raw_xml") or ""
        if not xml:
            return
        filename = "%s%s.xml" % (item.get("rnc_emisor") or "", item.get("encf") or item["id"])
        move.write({
            "l10n_do_ecf_edi_file": base64.b64encode(xml.encode("utf-8")),
            "l10n_do_ecf_edi_file_name": filename,
        })
        self.env["ir.attachment"].create({
            "name": filename,
            "datas": base64.b64encode(xml.encode("utf-8")),
            "res_model": "account.move",
            "res_id": move.id,
            "mimetype": "application/xml",
        })

    # -- Commercial approval ------------------------------------------------------

    def action_glecf_accept(self):
        for move in self:
            move._glecf_send_approval("aceptado")
        return True

    def action_glecf_reject(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Rechazar el e-CF"),
            "res_model": "glecf.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }

    def _glecf_send_approval(self, estado, motivo=None):
        self.ensure_one()
        if not self.glecf_received_id:
            raise UserError(_("Esta factura no vino de GLECF."))
        if self.glecf_approval_state and self.glecf_approval_supplier != "fallido":
            raise UserError(_("La aprobación comercial de este e-CF ya se envió."))
        self.company_id._glecf_check_ready()
        from .glecf_client import GlecfError

        try:
            answer = self.company_id._glecf_client().received_approval(
                self.glecf_received_id, estado, motivo
            )
        except GlecfError as error:
            raise UserError(error.message) from error
        emisor = answer.get("emisor") or {}
        if emisor.get("estado") == "entregado":
            message = _("Enviada a la DGII y al proveedor.")
        elif emisor.get("estado") == "fallido":
            message = _("No le llegó al proveedor: %s. Pulsa otra vez para reenviársela.") % (
                emisor.get("motivo") or "-")
        else:
            message = _("Enviada a la DGII.")
        self.write({
            "glecf_approval_state": estado,
            "glecf_approval_message": message,
            "glecf_approval_supplier": emisor.get("estado") or False,
        })
        body = _("Aprobación comercial: %(state)s. %(msg)s") % {
            "state": _("aceptada") if estado == "aceptado" else _("rechazada"), "msg": message}
        if motivo:
            body += " " + _("Motivo: %s") % motivo
        self.message_post(body=body)
        # A rejected bill must not reach the 606. A draft cannot be cancelled
        # (l10n_do_accounting forbids cancelling a fiscal document never
        # posted), so it is kept but can no longer be posted; a posted one is
        # left for the person to reverse, saying so.
        if estado == "rechazado":
            if self.state == "posted":
                self.message_post(body=_("Esta factura ya estaba publicada: anúlala si no corresponde."))
            else:
                self.message_post(body=_("Rechazada: no se podrá publicar. Puedes eliminar este borrador."))

    def _post(self, soft=True):
        rejected = self.filtered(lambda m: m.glecf_approval_state == "rechazado")
        if rejected:
            raise UserError(
                _("Rechazaste el e-CF %s ante la DGII: no se publica ni va al 606.")
                % ", ".join(rejected.mapped(lambda m: m.l10n_do_fiscal_number or m.name))
            )
        return super()._post(soft)
