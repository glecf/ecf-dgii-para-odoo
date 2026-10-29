from odoo import _, fields, models
from odoo.exceptions import UserError


class GlecfRejectWizard(models.TransientModel):
    """Reject a received e-CF: the DGII asks for a reason (up to 250 characters)."""

    _name = "glecf.reject.wizard"
    _description = "Rechazar un e-CF recibido"

    move_id = fields.Many2one("account.move", required=True, ondelete="cascade")
    reason = fields.Char(string="Motivo del rechazo", size=250, required=True)

    def action_reject(self):
        self.ensure_one()
        reason = " ".join((self.reason or "").split())
        if not reason:
            raise UserError(_("Escribe el motivo del rechazo."))
        self.move_id._glecf_send_approval("rechazado", reason[:250])
        return {"type": "ir.actions.act_window_close"}
