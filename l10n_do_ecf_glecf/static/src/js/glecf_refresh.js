/** @odoo-module **/
// Reload an invoice on screen when GLECF changes its e-CF status (2026-10-29).
// Before, the status only changed after reloading the page by hand.
import { router } from "@web/core/browser/router";
import { registry } from "@web/core/registry";

export const glecfRefreshService = {
    dependencies: ["bus_service", "action"],
    start(env, { bus_service, action }) {
        bus_service.subscribe("glecf_move_updated", ({ ids }) => {
            const controller = action.currentController;
            const props = controller && controller.props;
            if (!props || props.resModel !== "account.move") {
                return;
            }
            const viewType = controller.view && controller.view.type;
            // The address of the page says which invoice is on screen. The
            // props do not: after «Nuevo» they keep the invoice opened before,
            // or have none (2026-10-29).
            const current = router.current.resId;
            const resId = current === "new" ? null : Number(current) || props.resId;
            const shown = viewType === "list" || (viewType === "form" && ids.includes(resId));
            if (shown) {
                action.restore(controller.jsId);
            }
        });
    },
};

registry.category("services").add("glecf_refresh", glecfRefreshService);
