/** @odoo-module **/
// Reload an invoice on screen when GLECF changes its e-CF status (2026-10-29).
// Before, the status only changed after reloading the page by hand.
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
            const shown = viewType === "list" || (viewType === "form" && ids.includes(props.resId));
            if (shown) {
                action.restore(controller.jsId);
            }
        });
    },
};

registry.category("services").add("glecf_refresh", glecfRefreshService);
