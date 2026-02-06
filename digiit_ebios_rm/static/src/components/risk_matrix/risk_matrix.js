/** @odoo-module **/
import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class RiskMatrix extends Component {
    setup() {
        // Get the ORM service
        this.orm = useService("orm");
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            currentRisks: [],
            residualRisks: [],
            studyId: this.props.action.context?.active_id || this.env?.context?.study_id || false,
            isLoading: true,
            isCapturing: false,
            riskMatrixImage: null,
            cellColors: {
                '1': { '1': 'bg-teal-500', '2': 'bg-teal-500', '3': 'bg-orange-300', '4': 'bg-orange-300' },
                '2': { '1': 'bg-teal-500', '2': 'bg-teal-500', '3': 'bg-orange-300', '4': 'bg-red-500' },
                '3': { '1': 'bg-teal-500', '2': 'bg-orange-300', '3': 'bg-red-500', '4': 'bg-red-500' },
                '4': { '1': 'bg-orange-300', '2': 'bg-orange-300', '3': 'bg-red-500', '4': 'bg-red-500' }
            },
        });

        onWillStart(async () => {
            await this.fetchRiskTreatments();
        });

        onMounted(() => {
            this.renderTooltips();

            // Load html2canvas library
            const script = document.createElement('script');
            script.src = '/digiit_ebios_rm/static/src/libs/html2canvas.min.js';
            script.onload = () => {
                console.log('html2canvas loaded');
            };
            document.head.appendChild(script);
        });
    }

    async fetchRiskTreatments() {
        try {
            // Reset the state arrays before fetching new data
            this.state.currentRisks = [];
            this.state.residualRisks = [];

            const domain = [['study_id', '=', this.state.studyId]];

            // Use ORM service instead of RPC
            const riskTreatments = await this.orm.searchRead(
                'digiit.ebios_rm.risk.treatment',    // Model name
                domain,                          // Domain
                [                                // Fields to fetch
                    'identifier', 'severity', 'likelihood', 'risk_level',
                    'risidual_severity', 'risidual_likelihood', 'risidual_risk_level',
                    'treatment_strategy', 'risk_id', 'removed', 'id'
                ]
            );

            this.processRiskData(riskTreatments);
            this.state.isLoading = false;
        } catch (error) {
            console.error('Failed to fetch risk treatments:', error);
            this.state.isLoading = false;
        }
    }

    processRiskData(riskTreatments) {
        const currentRisks = [];
        const residualRisks = [];
        const riskLevels = {};

        riskTreatments.forEach((treatment) => {
            // Skip treatments marked as removed
            if (treatment.removed) return;

            // Use the identifier from the treatment
            const riskId = treatment.identifier || `R${treatment.id}`;

            // Store risk descriptions in riskLevels
            if (treatment.risk_id && treatment.risk_id[1]) {
                riskLevels[riskId] = { description: treatment.risk_id[1] };
            }

            // Current risk
            if (treatment.severity && treatment.likelihood) {
                currentRisks.push({
                    id: riskId,
                    severity: parseInt(treatment.severity),  // Convert to integer
                    likelihood: parseInt(treatment.likelihood),  // Convert to integer
                    riskLevel: treatment.risk_level,
                    description: treatment.risk_id && treatment.risk_id[1] ? treatment.risk_id[1] : ''
                });
            }

            // Residual risk
            if (treatment.risidual_severity && treatment.risidual_likelihood) {
                residualRisks.push({
                    id: riskId,
                    severity: parseInt(treatment.risidual_severity),  // Convert to integer
                    likelihood: parseInt(treatment.risidual_likelihood),  // Convert to integer
                    riskLevel: treatment.risidual_risk_level,
                    description: treatment.risk_id && treatment.risk_id[1] ? treatment.risk_id[1] : ''
                });
            } else if (treatment.severity && treatment.likelihood) {
                // If residual values are not specified, use current values
                residualRisks.push({
                    id: riskId,
                    severity: parseInt(treatment.severity),  // Convert to integer
                    likelihood: parseInt(treatment.likelihood),  // Convert to integer
                    riskLevel: treatment.risk_level,
                    description: treatment.risk_id && treatment.risk_id[1] ? treatment.risk_id[1] : ''
                });
            }
        });

        this.state.currentRisks = currentRisks;
        this.state.residualRisks = residualRisks;
        this.state.riskLevels = riskLevels;  // Update the risk levels from fetched data
    }

    renderTooltips() {
        // Initialize tooltips if using a library like tippy.js or bootstrap tooltips
        // This is a placeholder for tooltip initialization
    }

    getCellContent(severity, likelihood, riskType) {
        const risks = riskType === 'current' ? this.state.currentRisks : this.state.residualRisks;
        const cellRisks = risks.filter(
            risk => risk.severity === parseInt(severity) && risk.likelihood === parseInt(likelihood)
        );

        return cellRisks.map(risk => risk.id).join(' ');
    }

    getCellClass(severity, likelihood) {
        return this.state.cellColors[severity][likelihood] || 'bg-gray-200';
    }

    renderTooltipContent(severity, likelihood, riskType) {
        const risks = riskType === 'current' ? this.state.currentRisks : this.state.residualRisks;
        const cellRisks = risks.filter(
            risk => risk.severity === parseInt(severity) && risk.likelihood === parseInt(likelihood)
        );

        if (cellRisks.length === 0) return '';

        return cellRisks.map(risk => `${risk.id}: ${risk.description}`).join('\n');
    }

    async captureRiskMatrix() {
        if (typeof html2canvas === 'undefined') {
            this.notification.add('html2canvas library not loaded. Please refresh the page.', {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;

        try {
            const element = document.getElementById('risk-matrix-capture-area');

            if (!element) {
                this.notification.add('Risk matrix element not found', {
                    type: 'danger'
                });
                this.state.isCapturing = false;
                return;
            }

            const canvas = await html2canvas(element, {
                allowTaint: true,
                useCORS: true,
                scale: 3,
                logging: false,
                backgroundColor: '#ffffff'
            });

            const imageData = canvas.toDataURL('image/png');

            // Store the image data in state
            this.state.riskMatrixImage = imageData;

            await this.saveRiskMatrixImage(imageData);

            this.notification.add('Risk matrix captured successfully!', {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing risk matrix:', error);
            this.notification.add('Failed to capture risk matrix. Please try again.', {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
        }
    }

    async saveRiskMatrixImage(imageData) {
        try {
            const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

            if (this.state.studyId) {
                await this.orm.write('digiit.ebios_rm.study', [this.state.studyId], {
                    'risk_matrix_image': base64Data
                });
                console.log(`Risk matrix image saved to study ${this.state.studyId}`);
            }
        } catch (error) {
            console.error('Error saving risk matrix image:', error);
            throw error;
        }
    }


    goBack() {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            if (this.state.studyId) {
                this.action.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'digiit.ebios_rm.study',
                    res_id: this.state.studyId,
                    views: [[false, 'form']],
                    target: 'current',
                    mode: 'readonly',
                    clear_breadcrumb: true
                });
            } else {
                this.action.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'digiit.ebios_rm.study',
                    views: [[false, 'list']],
                    target: 'current',
                    clear_breadcrumb: true
                });
            }
        }
    }
}

// Define the template
RiskMatrix.template = 'digiit_ebios_rm_risk_matrix.RiskMatrix';

// Register the component as a client action
registry.category("actions").add("digiit_ebios_rm.risk_matrix", RiskMatrix);

export default RiskMatrix;