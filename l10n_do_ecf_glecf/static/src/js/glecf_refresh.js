/** @odoo-module **/
// Reload an invoice on screen when GLECF changes its e-CF status (2026-10-29).
// Before, the status only changed after reloading the page by hand.
// Odoo 16: the bus has no subscribe(); every notification arrives through
// one "notification" event and is filtered by its type.
import { registry } from "@web/core/registry";

export const glecfRefreshService = {
    dependencies: ["bus_service", "action", "router"],
    start(env, { bus_service, action, router }) {
        bus_service.addEventListener("notification", ({ detail: notifications }) => {
            for (const { type, payload } of notifications) {
                if (type !== "glecf_move_updated") {
                    continue;
                }
                const ids = (payload && payload.ids) || [];
                const controller = action.currentController;
                const props = controller && controller.props;
                if (!props || props.resModel !== "account.move") {
                    return;
                }
                const viewType = controller.view && controller.view.type;
                // The address of the page says which invoice is on screen. The
                // props do not: after «Nuevo» they keep the invoice opened before,
                // or have none (2026-10-29).
                const resId = Number(router.current.hash.id) || props.resId;
                const shown = viewType === "list" || (viewType === "form" && ids.includes(resId));
                if (shown) {
                    action.restore(controller.jsId);
                }
                return;
            }
        });
    },
};

registry.category("services").add("glecf_refresh", glecfRefreshService);
