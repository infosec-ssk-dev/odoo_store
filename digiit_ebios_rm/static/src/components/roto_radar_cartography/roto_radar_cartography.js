/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class ROTORadarCartography extends Component {
    setup() {
        super.setup();
        this.orm = useService('orm');
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            riskSources: [],
            pertinenceLevels: [],
            riskOrigins: [],
            targetObjectives: [],
            selectedPair: null,
            studyId: this.props.action.context?.active_id || this.env?.context?.study_id || false,
            isCapturing: false,
            riskOriginChartImage: null,
            targetObjectiveChartImage: null,
            legendImage: null,
        });

        onWillStart(async () => {
            // Get study_id from current context if not set
            if (!this.state.studyId) {
                const currentAction = await this.action.getCurrentAction();
                if (currentAction?.context?.active_id) {
                    this.state.studyId = currentAction.context.active_id;
                }
            }

            await this.loadData();
        });

        onMounted(() => {
            // Load html2canvas library
            const script = document.createElement('script');
            script.src = '/digiit_ebios_rm/static/src/libs/html2canvas.min.js';
            script.onload = () => {
                console.log('html2canvas loaded');
            };
            document.head.appendChild(script);
        });
    }

    async loadData() {
        if (!this.state.studyId) {
            console.warn("No study_id found, cannot load RO/TO data");
            return;
        }

        // Load pertinence levels (to know how many circles to draw)
        this.state.pertinenceLevels = await this.orm.searchRead(
            'digiit.ebios_rm.pertinence',
            [],
            ['id', 'name', 'number', 'color', 'retenu'],
            { order: 'number desc' } // Highest number = center (most critical)
        );

        // Load RO/TO pairs for this study - ONLY those with pertinence values
        const domain = [
            ['study_id', '=', this.state.studyId],
            '|',
            ['pertinence_retenue_id', '!=', false],
            ['pertinence_id', '!=', false]
        ];
        this.state.riskSources = await this.orm.searchRead(
            'digiit.ebios_rm.risk.source',
            domain,
            ['id', 'name', 'prefixed_identifier', 'source_of_risk_id', 'source_of_risk_name',
             'targeted_objectif_id', 'targeted_objectif_name', 'pertinence_retenue_id',
             'pertinence_id', 'held', 'description', 'justification']
        );

        // Extract unique Risk Origins and Target Objectives
        const originsSet = new Set();
        const objectivesSet = new Set();

        this.state.riskSources.forEach(rs => {
            if (rs.source_of_risk_name) {
                originsSet.add(JSON.stringify({
                    id: rs.source_of_risk_id[0],
                    name: rs.source_of_risk_name
                }));
            }
            if (rs.targeted_objectif_name) {
                objectivesSet.add(JSON.stringify({
                    id: rs.targeted_objectif_id[0],
                    name: rs.targeted_objectif_name
                }));
            }
        });

        this.state.riskOrigins = Array.from(originsSet).map(s => JSON.parse(s));
        this.state.targetObjectives = Array.from(objectivesSet).map(s => JSON.parse(s));
    }

    // LEFT CHART: Group by Risk Origins
    getRiskSourcesByOrigin() {
        const grouped = {};
        this.state.riskOrigins.forEach(origin => {
            grouped[origin.id] = {
                origin: origin,
                pairs: []
            };
        });

        this.state.riskSources.forEach(rs => {
            if (rs.source_of_risk_id && grouped[rs.source_of_risk_id[0]]) {
                grouped[rs.source_of_risk_id[0]].pairs.push(rs);
            }
        });

        return Object.values(grouped);
    }

    // RIGHT CHART: Group by Target Objectives
    getRiskSourcesByObjective() {
        const grouped = {};
        this.state.targetObjectives.forEach(objective => {
            grouped[objective.id] = {
                objective: objective,
                pairs: []
            };
        });

        this.state.riskSources.forEach(rs => {
            if (rs.targeted_objectif_id && grouped[rs.targeted_objectif_id[0]]) {
                grouped[rs.targeted_objectif_id[0]].pairs.push(rs);
            }
        });

        return Object.values(grouped);
    }

    // Calculate position for a pair in LEFT chart (by Risk Origin)
    calculatePositionByOrigin(pair, originIndex, pairIndexInSector) {
        const totalOrigins = this.state.riskOrigins.length;
        const anglePerSector = 360 / totalOrigins;
        const baseAngle = originIndex * anglePerSector;

        // Get pairs in this sector
        const pairsInSector = this.getRiskSourcesByOrigin()[originIndex].pairs.length;

        // Distribute pairs within sector
        const offsetRange = anglePerSector * 0.8; // Use 80% of sector to avoid overlap
        const positionRatio = pairsInSector > 1 ?
            (pairIndexInSector / (pairsInSector - 1)) - 0.5 : 0;
        const angleOffset = positionRatio * offsetRange;
        const finalAngle = baseAngle + (anglePerSector / 2) + angleOffset;

        // Calculate radius based on pertinence level
        const radius = this.calculateRadius(pair);

        return this.polarToCartesian(250, 250, radius, finalAngle);
    }

    // Calculate position for a pair in RIGHT chart (by Target Objective)
    calculatePositionByObjective(pair, objectiveIndex, pairIndexInSector) {
        const totalObjectives = this.state.targetObjectives.length;
        const anglePerSector = 360 / totalObjectives;
        const baseAngle = objectiveIndex * anglePerSector;

        // Get pairs in this sector
        const pairsInSector = this.getRiskSourcesByObjective()[objectiveIndex].pairs.length;

        // Distribute pairs within sector
        const offsetRange = anglePerSector * 0.8;
        const positionRatio = pairsInSector > 1 ?
            (pairIndexInSector / (pairsInSector - 1)) - 0.5 : 0;
        const angleOffset = positionRatio * offsetRange;
        const finalAngle = baseAngle + (anglePerSector / 2) + angleOffset;

        // Calculate radius based on pertinence level
        const radius = this.calculateRadius(pair);

        return this.polarToCartesian(250, 250, radius, finalAngle);
    }

    // Calculate radius based on pertinence level (center = highest)
    calculateRadius(pair) {
        const maxRadius = 200;
        const minRadius = 20;
        const pertinenceLevels = this.state.pertinenceLevels.length;

        // Get pertinence number (use pertinence_retenue_id first, fallback to pertinence_id)
        const pertinenceId = pair.pertinence_retenue_id ?
            pair.pertinence_retenue_id[0] :
            (pair.pertinence_id ? pair.pertinence_id[0] : null);

        if (!pertinenceId) return maxRadius; // Default to outer if no pertinence

        const pertinence = this.state.pertinenceLevels.find(p => p.id === pertinenceId);
        if (!pertinence) return maxRadius;

        const pertinenceNumber = pertinence.number;

        // Calculate radius step between levels
        const radiusStep = (maxRadius - minRadius) / pertinenceLevels;

        // Higher pertinence number = closer to center
        // Level 1 (lowest) -> maxRadius, Level 2 -> maxRadius - radiusStep, etc.
        const radius = maxRadius - ((pertinenceNumber - 1) * radiusStep);

        return radius;
    }

    // Convert polar coordinates to cartesian
    polarToCartesian(centerX, centerY, radius, angleInDegrees) {
        const angleInRadians = (angleInDegrees - 90) * Math.PI / 180.0;
        return {
            x: centerX + (radius * Math.cos(angleInRadians)),
            y: centerY + (radius * Math.sin(angleInRadians))
        };
    }

    // Get color for pair (red = retained, green = non-retained)
    getPairColor(pair) {
        return pair.held ? '#B5182E' : '#46D684';
    }

    // Get all concentric circles for pertinence levels
    getPertinenceCircles() {
        const maxRadius = 200;
        const minRadius = 20;
        const pertinenceLevels = this.state.pertinenceLevels.length;
        const radiusStep = (maxRadius - minRadius) / pertinenceLevels;

        return this.state.pertinenceLevels.map(level => {
            // Higher pertinence number = closer to center
            const radius = maxRadius - ((level.number - 1) * radiusStep);

            return {
                level: level,
                radius: radius
            };
        });
    }

    // Get sector labels positions for LEFT chart (Risk Origins)
    getOriginSectorLabels() {
        const totalOrigins = this.state.riskOrigins.length;
        const anglePerSector = 360 / totalOrigins;

        return this.state.riskOrigins.map((origin, index) => {
            const angle = index * anglePerSector + (anglePerSector / 2);
            const pos = this.polarToCartesian(250, 250, 220, angle);
            return {
                origin: origin,
                abbreviation: `SR${index + 1}`,
                x: pos.x,
                y: pos.y
            };
        });
    }

    // Get sector labels positions for RIGHT chart (Target Objectives)
    getObjectiveSectorLabels() {
        const totalObjectives = this.state.targetObjectives.length;
        const anglePerSector = 360 / totalObjectives;

        return this.state.targetObjectives.map((objective, index) => {
            const angle = index * anglePerSector + (anglePerSector / 2);
            const pos = this.polarToCartesian(250, 250, 220, angle);
            return {
                objective: objective,
                abbreviation: `OV${index + 1}`,
                x: pos.x,
                y: pos.y
            };
        });
    }

//    // Select a pair for details
//    selectPair(pair) {
//        this.state.selectedPair = pair;
//    }

    // Render LEFT chart data (positioned by Risk Origin)
    renderLeftChartData() {
        const groupedByOrigin = this.getRiskSourcesByOrigin();
        const positioned = [];

        groupedByOrigin.forEach((group, originIndex) => {
            group.pairs.forEach((pair, pairIndex) => {
                const pos = this.calculatePositionByOrigin(pair, originIndex, pairIndex);
                positioned.push({
                    ...pair,
                    x: pos.x,
                    y: pos.y
                });
            });
        });

        return positioned;
    }

    // Render RIGHT chart data (positioned by Target Objective)
    renderRightChartData() {
        const groupedByObjective = this.getRiskSourcesByObjective();
        const positioned = [];

        groupedByObjective.forEach((group, objectiveIndex) => {
            group.pairs.forEach((pair, pairIndex) => {
                const pos = this.calculatePositionByObjective(pair, objectiveIndex, pairIndex);
                positioned.push({
                    ...pair,
                    x: pos.x,
                    y: pos.y
                });
            });
        });

        return positioned;
    }

    truncateText(text, maxLength = 20) {
        if (!text) return '';
        return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
    }

    // Get abbreviation for risk origin (SR1, SR2, etc.)
    getOriginAbbreviation(originId) {
        const index = this.state.riskOrigins.findIndex(o => o.id === originId);
        return index >= 0 ? `SR${index + 1}` : '';
    }

    // Get abbreviation for target objective (OV1, OV2, etc.)
    getObjectiveAbbreviation(objectiveId) {
        const index = this.state.targetObjectives.findIndex(o => o.id === objectiveId);
        return index >= 0 ? `OV${index + 1}` : '';
    }

    async captureCartography(type) {
        if (typeof html2canvas === 'undefined') {
            this.notification.add('html2canvas library not loaded. Please refresh the page.', {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;
        const elementId = type === 'risk_origin' ? 'risk-origin-chart' : 'target-objective-chart';
        const element = document.getElementById(elementId);

        if (!element) {
            this.notification.add(`Chart element not found: ${elementId}`, {
                type: 'danger'
            });
            this.state.isCapturing = false;
            return;
        }

        try {
            const canvas = await html2canvas(element, {
                allowTaint: true,
                useCORS: true,
                scale: 2,
                logging: false,
                onclone: (clonedDoc) => {
                    const svgs = clonedDoc.querySelectorAll('svg');
                    svgs.forEach(svg => {
                        if (!svg.getAttribute('width')) {
                            svg.setAttribute('width', '500px');
                        }
                        if (!svg.getAttribute('height')) {
                            svg.setAttribute('height', '500px');
                        }
                    });
                }
            });

            const imageData = canvas.toDataURL('image/png');

            // Store the image data in state
            if (type === 'risk_origin') {
                this.state.riskOriginChartImage = imageData;
            } else {
                this.state.targetObjectiveChartImage = imageData;
            }

            await this.saveCartographyImage(type, imageData);

            this.notification.add(`${type === 'risk_origin' ? 'Risk Origin' : 'Target Objective'} chart captured successfully!`, {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing chart:', error);
            this.notification.add('Failed to capture chart. Please try again.', {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
        }
    }

    async saveCartographyImage(type, imageData) {
        try {
            const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

            if (this.state.studyId) {
                const fieldName = type === 'risk_origin' ? 'roto_risk_origin_chart_image' : 'roto_target_objective_chart_image';
                await this.orm.write('digiit.ebios_rm.study', [this.state.studyId], {
                    [fieldName]: base64Data
                });
                console.log(`${type} chart image saved to study ${this.state.studyId}`);
            }
        } catch (error) {
            console.error('Error saving chart image:', error);
            throw error;
        }
    }

    async captureAll() {
        if (typeof html2canvas === 'undefined') {
            this.notification.add('html2canvas library not loaded. Please refresh the page.', {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;

        try {
            // Capture risk origin chart
            await this.captureCartography('risk_origin');

            // Capture target objective chart
            await this.captureCartography('target_objective');

            // Capture legend
            const legendElement = document.getElementById('legend-section');
            if (legendElement) {
                const canvas = await html2canvas(legendElement, {
                    allowTaint: true,
                    useCORS: true,
                    scale: 2,
                    logging: false,
                });

                const imageData = canvas.toDataURL('image/png');
                const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

                // Store in state
                this.state.legendImage = imageData;

                if (this.state.studyId) {
                    await this.orm.write('digiit.ebios_rm.study', [this.state.studyId], {
                        'roto_legend_image': base64Data
                    });
                }
            }

            this.notification.add('All charts and legend captured successfully!', {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing all:', error);
            this.notification.add('Failed to capture. Please try again.', {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
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
ROTORadarCartography.template = 'digiit_ebios_rm.ROTORadarCartography';

// Register the component
registry.category("actions").add("digiit_ebios_rm.roto_radar_cartography", ROTORadarCartography);

export default ROTORadarCartography;