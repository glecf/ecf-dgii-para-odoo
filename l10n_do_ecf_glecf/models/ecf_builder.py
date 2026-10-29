"""Turn a posted Odoo invoice into the e-CF data the DGII expects.

The keys are the element names of the DGII e-CF format (Encabezado, IdDoc,
Emisor...). GLECF builds the XML, signs it and sends it, so this file only has
to get the data right. Every rule that would make the DGII reject a document is
checked here first, so the person posting the invoice learns it in Odoo.
"""

from datetime import datetime

import re

import pytz

from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round

DR_TZ = pytz.timezone("America/Santo_Domingo")

# e-CF types this version sends: all ten. 46 only in Dominican pesos so far.
SUPPORTED_TYPES = ("31", "32", "33", "34", "41", "43", "44", "45", "46", "47")
# Issued by the company itself on a vendor bill: purchases from informal
# suppliers, minor expenses and payments abroad. In them the "Comprador" of
# the e-CF is the supplier.
PURCHASE_TYPES = ("41", "43", "47")
# Types that carry the expiry date of the sequence.
TYPES_WITH_SEQUENCE_EXPIRY = ("31", "33", "41", "43", "44", "45", "46", "47")
# Types whose buyer must always have an RNC or cédula. A consumer invoice
# (32) and its notes (33, 34) only need it from RD$250,000.
TYPES_WITH_BUYER_ID = ("31", "41", "44", "45")
CONSUMER_ID_FROM = 250000
# Types whose buyer may be a foreigner with a foreign tax id.
TYPES_WITH_FOREIGN_ID = ("32", "33", "34", "46", "47")
MAX_LINES = 1000
# The e-mail pattern of the DGII format (CorreoValidationType): one address.
EMAIL = re.compile(r"^\w+([-+.]\w+)*@\w+([-.]\w+)*\.\w+([-.]\w+)*$")
# Types whose lines carry withholdings (Retencion), mandatory on every line.
TYPES_WITH_WITHHOLDING = ("41", "47")

# ITBIS rate -> DGII IndicadorFacturacion.
ITBIS_INDICATORS = {18.0: "1", 16.0: "2"}
ZERO_RATE = "3"
EXEMPT = "4"

MAX_ITEM_NAME = 80
MAX_ITEM_DESCRIPTION = 1000
MAX_REASON = 90


def ecf_type(move):
    """'31' for an E31 document, '' if the invoice is not an e-CF."""
    prefix = move.l10n_latam_document_type_id.doc_code_prefix or ""
    return prefix[1:] if prefix.startswith("E") else ""


def build_ecf_data(move, now=None):
    """The `ecf_data` GLECF receives for `move`."""
    move.ensure_one()
    tipo = ecf_type(move)
    _check_invoice(move, tipo)

    items, totals = _items_and_totals(move, tipo)
    header = {
        "Version": "1.0",
        "IdDoc": _id_doc(move, tipo),
        "Emisor": _issuer(move),
    }
    # A minor expense has no buyer: the e-CF backs the company's own spending.
    if tipo != "43":
        header["Comprador"] = _buyer(move, tipo)
    header["Totales"] = totals
    data = {
        "Encabezado": header,
        "DetallesItems": items,
        "FechaHoraFirma": _datetime(now or datetime.now(pytz.utc)),
    }
    if tipo in ("33", "34"):
        data["InformacionReferencia"] = _reference(move, tipo)
    return data


# -- Checks ------------------------------------------------------------------


def _check_invoice(move, tipo):
    if tipo not in SUPPORTED_TYPES:
        raise UserError(
            _("%(doc)s: este módulo todavía no envía el tipo de e-CF %(type)s.")
            % {"doc": move.name, "type": tipo or "-"}
        )
    if move.currency_id != move.company_id.currency_id or move.currency_id.name != "DOP":
        raise UserError(
            _("%s: esta versión solo envía facturas en pesos dominicanos (DOP).") % move.name
        )
    if not move.l10n_do_fiscal_number:
        raise UserError(_("%s no tiene número e-NCF.") % move.name)
    if not move.invoice_date:
        raise UserError(_("%s no tiene fecha de factura.") % move.name)
    if len(move.invoice_line_ids.filtered(lambda line: line.display_type == "product")) > MAX_LINES:
        raise UserError(_("%(doc)s tiene más de %(max)s líneas, el máximo de un e-CF.") % {"doc": move.name, "max": MAX_LINES})


# -- Header ------------------------------------------------------------------


def _id_doc(move, tipo):
    id_doc = {
        "TipoeCF": tipo,
        "eNCF": move.l10n_do_fiscal_number,
    }
    if tipo in TYPES_WITH_SEQUENCE_EXPIRY:
        expiry = _sequence_expiry(move)
        if not expiry:
            raise UserError(
                _("%s: indica la fecha de vencimiento de la secuencia e-NCF en Ajustes > Facturación > "
                  "Secuencias e-NCF antes de enviarla.") % move.name
            )
        if expiry < datetime.now(DR_TZ).date():
            raise UserError(
                _("%(doc)s: la secuencia E%(type)s venció el %(date)s y la DGII rechazaría el e-CF. "
                  "Pide una nueva a la DGII y pon su fecha en Ajustes > Facturación > Secuencias e-NCF.")
                % {"doc": move.name, "type": tipo, "date": _date(expiry)}
            )
        id_doc["FechaVencimientoSecuencia"] = _date(expiry)
    if tipo == "34":
        id_doc["IndicadorNotaCredito"] = _credit_note_indicator(move)
    # Amounts are sent without ITBIS. Minor expenses, exports and payments
    # abroad carry no ITBIS, and their format has no such field.
    if tipo not in ("43", "46", "47"):
        id_doc["IndicadorMontoGravado"] = "0"
    # Income type only exists on sales.
    if tipo not in PURCHASE_TYPES:
        id_doc["TipoIngresos"] = move.l10n_do_income_type or "01"

    # Cash if it is paid, or if it is due the same day: the job sends it right
    # after posting, usually before the payment is registered.
    cash = move.payment_state in ("paid", "in_payment") or (
        not move.invoice_date_due or move.invoice_date_due <= move.invoice_date
    )
    if tipo == "43":
        # The format of a minor expense has no payment deadline.
        if cash:
            id_doc["TipoPago"] = "1"
    elif tipo == "34" or cash:
        id_doc["TipoPago"] = "1"
    else:
        # On credit: the DGII requires the payment deadline, never before the
        # issue date.
        id_doc["TipoPago"] = "2"
        due = max(move.invoice_date_due or move.invoice_date, move.invoice_date)
        id_doc["FechaLimitePago"] = _date(due)
    return id_doc


def _sequence_expiry(move):
    """The expiry of the sequence as it is set now, not as it was copied to the
    invoice: l10n_do_accounting copies it only when the type or the journal
    change, so a date corrected later would never reach an invoice already
    posted."""
    sequence = move.journal_id.l10n_do_document_type_ids.filtered(
        lambda doc: doc.l10n_latam_document_type_id == move.l10n_latam_document_type_id
    )[:1]
    return sequence.l10n_do_ncf_expiration_date or move.l10n_do_ncf_expiration_date


def _email(text):
    """The first valid address, or None: the field is optional, and an address
    the DGII format does not take would reject the whole e-CF."""
    for candidate in re.split(r"[,;\s]+", text or ""):
        candidate = candidate.strip("<>\"'")
        if len(candidate) <= 80 and EMAIL.match(candidate):
            return candidate
    return None


def _buyer_id_required(move, tipo):
    if tipo in TYPES_WITH_BUYER_ID:
        return True
    if tipo == "32":
        return abs(move.amount_total) >= CONSUMER_ID_FROM
    if tipo in ("33", "34"):
        origin = _origin(move)
        if ecf_type(origin) == "32":
            return abs(origin.amount_total) >= CONSUMER_ID_FROM
        return True
    return False


def _issuer(move):
    company = move.company_id
    rnc = _digits(company.vat)
    if not rnc:
        raise UserError(_("Indica el RNC de %s antes de enviar e-CF.") % company.name)
    address = _one_line(" ".join(filter(None, [company.street, company.street2, company.city])))
    if not address:
        raise UserError(_("Indica la dirección de %s antes de enviar e-CF.") % company.name)
    issuer = {
        "RNCEmisor": rnc,
        "RazonSocialEmisor": _one_line(company.name)[:150],
        "DireccionEmisor": address[:100],
        "FechaEmision": _date(move.invoice_date),
    }
    email = _email(company.email)
    if email:
        issuer["CorreoEmisor"] = email
    return issuer


def _buyer(move, tipo):
    partner = move.commercial_partner_id
    if tipo in ("46", "47") and not _is_dominican(partner):
        return _foreign_buyer(move, partner, tipo)
    buyer_id = _digits(partner.vat)
    foreign_id = ""
    if tipo in TYPES_WITH_FOREIGN_ID and not _is_dominican(partner):
        foreign_id = "".join(char for char in (partner.vat or "") if char.isalnum())[:20]
    if _buyer_id_required(move, tipo) and len(buyer_id) not in (9, 11) and not foreign_id:
        who = _("el proveedor") if tipo in PURCHASE_TYPES else _("el cliente")
        message = _("%(doc)s: %(who)s %(name)s necesita un RNC (9 dígitos) o una cédula (11 dígitos) válidos.")
        if tipo in ("32", "33", "34"):
            message += " " + _("Desde RD$250,000 la DGII pide identificar al cliente.")
        raise UserError(message % {"doc": move.name, "who": who, "name": partner.name})
    if tipo == "47":
        raise UserError(
            _("%(doc)s: un pago al exterior (E47) es a un proveedor de fuera del país, y %(name)s es de "
              "República Dominicana. Revisa el país del proveedor.")
            % {"doc": move.name, "name": partner.name}
        )
    buyer = {}
    if len(buyer_id) in (9, 11) and not foreign_id:
        buyer["RNCComprador"] = buyer_id
    elif foreign_id:
        buyer["IdentificadorExtranjero"] = foreign_id
    if partner.name:
        buyer["RazonSocialComprador"] = _one_line(partner.name)[:150]
    email = _email(partner.email)
    if email:
        buyer["CorreoComprador"] = email
    address = _one_line(" ".join(filter(None, [partner.street, partner.street2, partner.city])))
    if address:
        buyer["DireccionComprador"] = address[:100]
    return buyer


def _is_dominican(partner):
    return not partner.country_id or partner.country_id.code == "DO"


def _foreign_buyer(move, partner, tipo):
    """Buyer of an export (46) or supplier of a payment abroad (47).

    It has no RNC: the DGII takes its foreign tax id, if any. A payment abroad
    carries nothing else.
    """
    if not partner.name:
        raise UserError(_("%s: indica el nombre del cliente o proveedor extranjero.") % move.name)
    buyer = {}
    foreign_id = "".join(char for char in (partner.vat or "") if char.isalnum())[:20]
    if foreign_id:
        buyer["IdentificadorExtranjero"] = foreign_id
    buyer["RazonSocialComprador"] = _one_line(partner.name)[:150]
    if tipo == "46":
        email = _email(partner.email)
        if email:
            buyer["CorreoComprador"] = email
        address = _one_line(" ".join(filter(None, [partner.street, partner.street2, partner.city])))
        if address:
            buyer["DireccionComprador"] = address[:100]
        # The DGII only takes letters and spaces here.
        country = " ".join("".join(c for c in partner.country_id.name if c.isalpha() or c == " ").split())
        if country:
            buyer["PaisComprador"] = country[:60]
    return buyer


# -- Lines and totals --------------------------------------------------------


def _items_and_totals(move, tipo):
    company = move.company_id
    currency = move.currency_id
    groups = _tax_groups(move)

    lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
    if not lines:
        raise UserError(_("%s no tiene líneas.") % move.name)

    items = []
    bases = {"1": 0.0, "2": 0.0, ZERO_RATE: 0.0, EXEMPT: 0.0}
    withheld = {"itbis": 0.0, "isr": 0.0}
    for number, line in enumerate(lines, start=1):
        indicator = _line_indicator(move, line, tipo, groups)
        # The DGII takes quantities with two decimals: everything else is
        # computed on the quantity as it is sent.
        quantity = float_round(line.quantity, precision_digits=2)
        if float_compare(quantity, 0, precision_digits=2) <= 0:
            raise UserError(_("%(doc)s: la línea «%(line)s» necesita una cantidad mayor que cero (con dos decimales como mucho).") % {"doc": move.name, "line": line.name})
        if float_compare(line.price_unit, 0, precision_digits=6) < 0 or float_compare(line.discount, 0, precision_digits=2) < 0:
            raise UserError(
                _("%(doc)s: la línea «%(line)s» tiene un precio o un descuento negativo. Un e-CF no admite "
                  "líneas en negativo: aplica el descuento en la línea a la que corresponde, o emite una "
                  "nota de crédito.") % {"doc": move.name, "line": line.name}
            )

        net = currency.round(abs(line.price_subtotal))
        if tipo == "43":
            # The ITBIS of a minor expense cannot be claimed: it goes inside
            # the price, and the whole line is exempt.
            net = currency.round(abs(line.price_total))
        if line.discount and float_compare(line.discount, 100, precision_digits=2) >= 0:
            raise UserError(_("%(doc)s: la línea «%(line)s» tiene un descuento del 100%%.") % {"doc": move.name, "line": line.name})
        # Minor expenses and payments abroad have no discounts in their
        # format: the line goes at its net price.
        with_discount = tipo not in ("43", "47") and line.discount
        gross = currency.round(net / (1 - (line.discount or 0) / 100)) if with_discount else net
        discount = currency.round(gross - net)
        unit_price = _unit_price(gross, quantity)

        service = line.product_id.type == "service" or not line.product_id
        item = {
            "NumeroLinea": str(number),
            "IndicadorFacturacion": indicator,
        }
        if tipo in TYPES_WITH_WITHHOLDING:
            item["Retencion"] = _withholding(move, line, tipo, groups, indicator, service, withheld)
        item.update({
            **_item_names(line.name or line.product_id.display_name),
            "IndicadorBienoServicio": "2" if service or tipo == "47" else "1",
            "CantidadItem": _number(quantity, 2),
            "PrecioUnitarioItem": unit_price,
        })
        if not float_is_zero(discount, precision_digits=2):
            item["DescuentoMonto"] = _amount(discount)
            item["TablaSubDescuento"] = {
                "SubDescuento": [{
                    "TipoSubDescuento": "%",
                    "SubDescuentoPorcentaje": _number(line.discount, 2),
                    "MontoSubDescuento": _amount(discount),
                }]
            }
        item["MontoItem"] = _amount(net)
        items.append(item)
        bases[indicator] += net

    taxes = _itbis_amounts(move, groups["itbis"]) if tipo != "43" else {"1": 0.0, "2": 0.0}
    totals = {}
    taxed = bases["1"] + bases["2"] + bases[ZERO_RATE]
    if not float_is_zero(taxed, precision_digits=2):
        totals["MontoGravadoTotal"] = _amount(taxed)
    for indicator, rate in (("1", "18"), ("2", "16")):
        if not float_is_zero(bases[indicator], precision_digits=2):
            totals["MontoGravadoI%s" % indicator] = _amount(bases[indicator])
            totals["ITBIS%s" % indicator] = rate
            totals["TotalITBIS%s" % indicator] = _amount(taxes[indicator])
    if not float_is_zero(bases[ZERO_RATE], precision_digits=2):
        totals["MontoGravadoI3"] = _amount(bases[ZERO_RATE])
        totals["ITBIS3"] = "0"
    if not float_is_zero(bases[EXEMPT], precision_digits=2):
        totals["MontoExento"] = _amount(bases[EXEMPT])
    total_itbis = taxes["1"] + taxes["2"]
    if not float_is_zero(total_itbis, precision_digits=2) or tipo == "46":
        totals["TotalITBIS"] = _amount(total_itbis)
    if tipo == "46":
        totals["TotalITBIS3"] = _amount(0)

    total = currency.round(taxed + bases[EXEMPT] + total_itbis)
    # Odoo takes the withholdings off the invoice total; the e-CF total does
    # not: they are reported apart.
    odoo_total = currency.round(abs(move.amount_total) + withheld["itbis"] + withheld["isr"])
    if float_compare(total, odoo_total, precision_digits=2) != 0:
        raise UserError(
            _("%(doc)s: el total del e-CF (%(ecf)s) no cuadra con el de la factura (%(odoo)s). "
              "Esta versión solo admite ITBIS 18%%, ITBIS 16%%, líneas exentas y retenciones de ITBIS e ISR.")
            % {"doc": move.name, "ecf": _amount(total), "odoo": _amount(odoo_total)}
        )
    totals["MontoTotal"] = _amount(total)
    if tipo == "41":
        totals["TotalITBISRetenido"] = _amount(withheld["itbis"])
    if tipo in TYPES_WITH_WITHHOLDING:
        totals["TotalISRRetencion"] = _amount(withheld["isr"])
    return items, totals


def _tax_groups(move):
    company = move.company_id
    groups = {
        name: move.env.ref("account.%s_tax_group_%s" % (company.id, name), raise_if_not_found=False)
        for name in ("itbis", "isr")
    }
    if not groups["itbis"]:
        # Without the Dominican chart of accounts every tax would look like
        # "something other than ITBIS", with a confusing message.
        raise UserError(
            _("%s: no encuentro el grupo de impuestos ITBIS del plan contable dominicano. "
              "Instala el plan contable de República Dominicana en la empresa.") % move.name
        )
    return groups


def _is_withholding(tax, groups):
    """Odoo keeps withholdings as negative ITBIS or ISR taxes."""
    return tax.amount < 0 and tax.tax_group_id in (groups["itbis"], groups["isr"]) and bool(tax.tax_group_id)


def _line_indicator(move, line, tipo, groups):
    itbis_group = groups["itbis"]
    withholdings = line.tax_ids.filtered(lambda tax: _is_withholding(tax, groups))
    if withholdings and tipo not in TYPES_WITH_WITHHOLDING:
        raise UserError(
            _("%(doc)s: la línea «%(line)s» lleva retenciones (%(taxes)s), y este tipo de e-CF no las admite. "
              "Solo las llevan las compras (E41) y los pagos al exterior (E47).")
            % {"doc": move.name, "line": line.name, "taxes": ", ".join(withholdings.mapped("name"))}
        )
    if tipo == "47" and withholdings.filtered(lambda tax: tax.tax_group_id == itbis_group):
        raise UserError(
            _("%(doc)s: un pago al exterior (E47) solo retiene ISR, no ITBIS (línea «%(line)s»).")
            % {"doc": move.name, "line": line.name}
        )
    itbis = line.tax_ids.filtered(lambda tax: itbis_group and tax.tax_group_id == itbis_group) - withholdings
    others = line.tax_ids - itbis - withholdings
    if others.filtered(lambda tax: tax.amount):
        raise UserError(
            _("%(doc)s: la línea «%(line)s» tiene impuestos que no son ITBIS ni retenciones (%(taxes)s). "
              "Esta versión todavía no envía impuestos selectivos ni otros cargos.")
            % {"doc": move.name, "line": line.name, "taxes": ", ".join(others.mapped("name"))}
        )
    rated = itbis.filtered(lambda tax: tax.amount)
    if tipo in ("43", "47"):
        # Minor expenses and payments abroad are always exempt.
        if tipo == "47" and rated:
            raise UserError(
                _("%(doc)s: un pago al exterior (E47) no lleva ITBIS (línea «%(line)s»).")
                % {"doc": move.name, "line": line.name}
            )
        return EXEMPT
    if tipo == "46":
        if rated:
            raise UserError(
                _("%(doc)s: una exportación (E46) va a tasa cero: quita el ITBIS de la línea «%(line)s».")
                % {"doc": move.name, "line": line.name}
            )
        return ZERO_RATE
    if not rated:
        return EXEMPT
    if len(rated) > 1 or rated.amount_type != "percent" or rated.amount not in ITBIS_INDICATORS:
        raise UserError(
            _("%(doc)s: la línea «%(line)s» debe llevar un solo ITBIS, del 18%% o del 16%%, o ninguno.")
            % {"doc": move.name, "line": line.name}
        )
    if tipo == "44":
        raise UserError(
            _("%(doc)s: una factura de régimen especial (E44) no puede cobrar ITBIS (línea «%(line)s»).")
            % {"doc": move.name, "line": line.name}
        )
    return ITBIS_INDICATORS[rated.amount]


def _withholding(move, line, tipo, groups, indicator, service, withheld):
    """The Retencion of a line of a purchase (41) or a payment abroad (47).

    The amounts are the ones Odoo computes with the withholding taxes of the
    line; the module does not decide any rate.
    """
    currency = move.currency_id
    amounts = {"itbis": 0.0, "isr": 0.0}
    taxes = line.tax_ids.filtered(lambda tax: _is_withholding(tax, groups))
    if taxes:
        price = line.price_unit * (1 - (line.discount or 0) / 100)
        computed = line.tax_ids.compute_all(
            price, currency, line.quantity, product=line.product_id, partner=move.partner_id
        )["taxes"]
        for result in computed:
            tax = taxes.filtered(lambda t: t.id == result["id"])
            if tax:
                key = "itbis" if tax.tax_group_id == groups["itbis"] else "isr"
                amounts[key] += abs(result["amount"])
    amounts = {key: currency.round(value) for key, value in amounts.items()}
    if indicator == EXEMPT and not float_is_zero(amounts["itbis"], precision_digits=2):
        raise UserError(
            _("%(doc)s: la línea «%(line)s» retiene ITBIS pero no lleva ITBIS. Quita la retención de "
              "ITBIS o añade el ITBIS a la línea.") % {"doc": move.name, "line": line.name}
        )
    # A payment abroad is always a service for the DGII.
    if tipo == "41" and not service and not float_is_zero(amounts["isr"], precision_digits=2):
        raise UserError(
            _("%(doc)s: la línea «%(line)s» retiene ISR, y la DGII solo lo admite en servicios. "
              "Marca el producto como servicio o quita la retención de ISR.")
            % {"doc": move.name, "line": line.name}
        )
    withheld["itbis"] += amounts["itbis"]
    withheld["isr"] += amounts["isr"]
    retention = {"IndicadorAgenteRetencionoPercepcion": "1"}
    if tipo == "41":
        retention["MontoITBISRetenido"] = _amount(amounts["itbis"])
        if service:
            retention["MontoISRRetenido"] = _amount(amounts["isr"])
    else:
        retention["MontoISRRetenido"] = _amount(amounts["isr"])
    return retention


def _itbis_amounts(move, itbis_group):
    """ITBIS actually charged on the invoice, by DGII indicator."""
    amounts = {"1": 0.0, "2": 0.0}
    for line in move.line_ids.filtered(lambda line: line.tax_line_id):
        tax = line.tax_line_id
        if itbis_group and tax.tax_group_id == itbis_group and tax.amount in ITBIS_INDICATORS:
            amounts[ITBIS_INDICATORS[tax.amount]] += abs(line.amount_currency)
    return {key: move.currency_id.round(value) for key, value in amounts.items()}


def _unit_price(gross, quantity):
    """Unit price with 2 decimals when exact, else 4 (the DGII allows 4)."""
    for digits in (2, 4):
        price = float_round(gross / quantity, precision_digits=digits)
        if float_compare(price * quantity, gross, precision_digits=2) == 0:
            return _number(price, digits)
    return _number(gross / quantity, 4)


def _item_names(text):
    text = _one_line(text)
    if len(text) <= MAX_ITEM_NAME:
        return {"NombreItem": text or "-"}
    return {
        "NombreItem": text[:MAX_ITEM_NAME],
        "DescripcionItem": text[MAX_ITEM_NAME:MAX_ITEM_NAME + MAX_ITEM_DESCRIPTION],
    }


# -- Debit and credit notes --------------------------------------------------


def _origin(move):
    origin = move.reversed_entry_id or getattr(move, "debit_origin_id", move.browse())
    if not origin and move.l10n_do_origin_ncf:
        origin = move.search([
            ("company_id", "=", move.company_id.id),
            ("l10n_do_fiscal_number", "=", move.l10n_do_origin_ncf),
            ("state", "=", "posted"),
        ], limit=1)
    return origin


def _reference(move, tipo):
    origin = _origin(move)
    origin_ncf = move.l10n_do_origin_ncf or origin.l10n_do_fiscal_number
    if not origin_ncf or not origin.invoice_date:
        raise UserError(
            _("%s: la nota debe apuntar a una factura publicada de esta empresa (campo «Modifica»).") % move.name
        )
    code = move.l10n_do_ecf_modification_code
    if not code:
        same_total = float_compare(abs(move.amount_total), abs(origin.amount_total), precision_digits=2) == 0
        code = "1" if tipo == "34" and same_total else "3"
    reference = {
        "NCFModificado": origin_ncf,
        "FechaNCFModificado": _date(origin.invoice_date),
        "CodigoModificacion": code,
    }
    reason = _one_line(move.ref or "")[:MAX_REASON]
    if reason:
        reference["RazonModificacion"] = reason
    return reference


def _credit_note_indicator(move):
    """0 if the credit note is within 30 days of the invoice, 1 if later."""
    origin = _origin(move)
    if origin.invoice_date and (move.invoice_date - origin.invoice_date).days > 30:
        return "1"
    return "0"


# -- Formats -----------------------------------------------------------------


def _date(value):
    return value.strftime("%d-%m-%Y")


def _datetime(value):
    if value.tzinfo is None:
        value = pytz.utc.localize(value)
    return value.astimezone(DR_TZ).strftime("%d-%m-%Y %H:%M:%S")


def _amount(value):
    return "%.2f" % float_round(value, precision_digits=2)


def _number(value, digits):
    text = ("%." + str(digits) + "f") % float_round(value, precision_digits=digits)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _digits(text):
    return "".join(char for char in (text or "") if char.isdigit())


def _one_line(text):
    """Line breaks and repeated spaces become one space: some e-CF readers
    reject signed documents whose texts contain line breaks. Control
    characters, which XML does not allow, go away."""
    text = "".join(char for char in (text or "") if char.isprintable() or char.isspace())
    return " ".join(text.split())
