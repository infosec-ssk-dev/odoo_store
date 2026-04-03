/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

const SIGNING_METHODS = [
    { key: "digigo", label: "DigiGo", showFlag: "show_digigo" },
    { key: "token", label: "Certificat ID-Trust (USB)", showFlag: "show_token" },
    { key: "enterprise", label: "Cachet électronique Enterprise-ID", showFlag: "show_enterprise" },
];

class SigningMethodToggle extends Component {
    static template = "digiit_elfatoora_ttn_base.SigningMethodToggle";
    static props = { ...standardFieldProps };

    get visibleMethods() {
        return SIGNING_METHODS.filter((m) => this.props.record.data[m.showFlag]);
    }

    get currentMethod() {
        return this.props.record.data[this.props.name];
    }

    select(key) {
        if (this.currentMethod !== key) {
            this.props.record.update({ [this.props.name]: key });
        }
    }
}

registry.category("fields").add("signing_method_toggle", {
    component: SigningMethodToggle,
});

