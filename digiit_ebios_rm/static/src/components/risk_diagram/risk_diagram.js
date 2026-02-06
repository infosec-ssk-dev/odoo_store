/** @odoo-module **/

import { Component, useState, onWillStart, onMounted} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class RiskDiagram extends Component {
    static props = {
        action: Object,
        actionId: Number,
        updateActionState: Function,
        className: { type: String, optional: true },
    };

    setup() {
        this.state = useState({
            studies: [],
            selectedStudyId: null,
            selectedStudy: null,
            workshops: [
                { id: 1, name: "Atelier 1", code: "workshop1", title: "Cadrage et socle de sécurité", color: "#e88d8d" },
                { id: 2, name: "Atelier 2", code: "workshop2", title: "Sources de risque", color: "#f0cf8c" },
                { id: 3, name: "Atelier 3", code: "workshop3", title: "Scénarios stratégiques", color: "#8cd6f0" },
                { id: 4, name: "Atelier 4", code: "workshop4", title: "Scénarios opérationnels", color: "#8cf0a3" },
                { id: 5, name: "Atelier 5", code: "workshop5", title: "Traitement du risque", color: "#f0aa8c" },
            ],
            activeWorkshop: null
        });

        this.orm = useService("orm");
        this.actionService = useService("action");

        onWillStart(async () => {
            await this.loadStudies();
        });

        onMounted(() => {
            if (this.state.studies.length > 0) {
                this.state.selectedStudyId = this.state.studies[0].id;
                this.loadStudyDetails();
            }
        });
    }

    async loadStudies() {
        try {
            const studies = await this.orm.searchRead(
                "digiit.ebios_rm.study",
                [],
                ["id", "name", "owner_company", "state"],
                {
                    order: "id desc"  // Sort by ID descending (most recent first)
                }
            );
            this.state.studies = studies;
        } catch (error) {
            console.error("Error loading studies:", error);
        }
    }

    async loadStudyDetails() {
        if (!this.state.selectedStudyId) return;

        try {
            const study = await this.orm.read(
                "digiit.ebios_rm.study",
                [this.state.selectedStudyId],
                ["name", "state", "strategic_cycle", "operational_cycle", "owner_company"]
            );

            if (study && study.length > 0) {
                this.state.selectedStudy = study[0];
                this.state.activeWorkshop = study[0].state;
            }
        } catch (error) {
            console.error("Error loading study details:", error);
        }
    }

    onStudyChange(ev) {
        this.state.selectedStudyId = parseInt(ev.target.value);
        this.loadStudyDetails();
    }

    async navigateToWorkshop(workshop) {
        if (!this.state.selectedStudyId) return;

        // First update the state of the study
        await this.orm.write("digiit.ebios_rm.study", [this.state.selectedStudyId], {
            state: workshop.code
        });

        // Then navigate to the form view
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "digiit.ebios_rm.study",
            res_id: this.state.selectedStudyId,
            views: [[false, "form"]],
            target: "current",
            context: {
                active_id: this.state.selectedStudyId,
                form_view_initial_mode: "edit",
                force_detailed_view: true
            }
        });
    }

    isWorkshopActive(workshop) {
        // Current workshop is active
        if (this.state.activeWorkshop === workshop.code) {
            return true;
        }

        // Check if we've passed this workshop in the sequence
        const workshopSequence = {
            workshop1: 1,
            workshop2: 2,
            workshop3: 3,
            workshop4: 4,
            workshop5: 5
        };

        const currentWorkshopIndex = workshopSequence[this.state.activeWorkshop] || 0;
        const thisWorkshopIndex = workshopSequence[workshop.code] || 0;

        return thisWorkshopIndex < currentWorkshopIndex;
    }

    isWorkshopEnabled(workshop) {
        if (!this.state.selectedStudyId) return false;
        return true; // In most cases we want all workshops to be clickable
    }
}

// Define the template
RiskDiagram.template = 'digiit_ebios_rm_risk_diagram.RiskDiagram';

// Register the component
registry.category("actions").add("digiit_ebios_rm_risk_diagram.risk_diagram", RiskDiagram);

export default RiskDiagram;