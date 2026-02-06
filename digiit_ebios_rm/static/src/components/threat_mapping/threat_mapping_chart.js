/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class EcosystemThreatMap extends Component {
    setup() {
        super.setup();
        this.orm = useService('orm');
        this.action = useService('action');
        this.notification = useService('notification');

        this.state = useState({
            stakeholders: [],
            selectedStakeholder: null,
            studyId: this.props.action.context?.active_id || this.env?.context?.study_id || false,
            actualCartographyImage: null,
            residualCartographyImage: null,
            cartography_legend_image: null,
            isCapturing: false
        });

        this.tooltip = useState({
            visible: false,
            x: 0,
            y: 0,
            content: null
        });

        onWillStart(async () => {
            if (!this.state.studyId) {
                const currentAction = await this.action.getCurrentAction();
                if (currentAction?.context?.active_id) {
                    this.state.studyId = currentAction.context.active_id;
                }
            }

            await this.loadEcosystemStakeholders();
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

    async loadEcosystemStakeholders() {
        if (!this.state.studyId) {
            console.warn("No study_id found, cannot load ecosystem stakeholders");
            return;
        }

        const domain = [
            ['study_id', '=', this.state.studyId],
//            ['critique', '=', true]
        ];

        const allCritiqueStakeholders = await this.orm.searchRead(
            'digiit.ebios_rm.ecosystem',
            domain,
            ['id', 'stakeholder_id', 'category', 'exposition', 'cyber_reliability',
             'threat_level', 'residual_threat_level', 'description', 'color_index',
             'zone_index', 'stakeholder_size', 'abbreviation', 'full_stakeholder_name'],
            { order: 'threat_level desc' }
        );

        console.log(`Total critique stakeholders found: ${allCritiqueStakeholders.length}`);

        const categories = ['prestataires', 'partenaires', 'customers', 'personnels'];
        const topStakeholders = [];

        for (const category of categories) {
            const categoryStakeholders = allCritiqueStakeholders.filter(
                s => s.category === category
            );

            console.log(`Category ${category}: ${categoryStakeholders.length} stakeholders`);

            if (categoryStakeholders.length === 0) continue;

            const sortedStakeholders = categoryStakeholders.sort((a, b) => b.threat_level - a.threat_level);

            const criticalThreat = sortedStakeholders.filter(s => s.threat_level >= 15);
            const highThreat = sortedStakeholders.filter(s => s.threat_level >= 10 && s.threat_level < 15);
            const mediumThreat = sortedStakeholders.filter(s => s.threat_level >= 5 && s.threat_level < 10);
            const lowThreat = sortedStakeholders.filter(s => s.threat_level < 5);

            const selectedStakeholders = [
                ...criticalThreat.slice(0, 1),
                ...highThreat.slice(0, 3),
                ...mediumThreat.slice(0, 5),
                ...lowThreat.slice(0, 7)
            ];

            console.log(`  Total selected for ${category}: ${selectedStakeholders.length}`);

            topStakeholders.push(...selectedStakeholders);
        }

        console.log(`Final stakeholders for cartography: ${topStakeholders.length}`);

        this.state.stakeholders = topStakeholders.map(ecosystem => ({
            ...ecosystem,
            name: ecosystem.full_stakeholder_name || "Unknown",
            abbreviation: ecosystem.abbreviation || "?"
        }));

        console.log("Stakeholder data loaded:", this.state.stakeholders.map(s => ({
            abbr: s.abbreviation,
            name: s.name
        })));
    }

    renderThreatChart(threatType) {
        const markers = [];

        const stakeholdersByCategory = {};
        this.state.stakeholders.forEach(stakeholder => {
            if (!stakeholdersByCategory[stakeholder.category]) {
                stakeholdersByCategory[stakeholder.category] = [];
            }
            stakeholdersByCategory[stakeholder.category].push(stakeholder);
        });

        Object.keys(stakeholdersByCategory).forEach(category => {
            const categoryStakeholders = stakeholdersByCategory[category];
            const totalInCategory = categoryStakeholders.length;

            categoryStakeholders.forEach((stakeholder, index) => {
                const positionRatio = totalInCategory === 1 ? 0.5 : (index + 1) / (totalInCategory + 1);

                const marker = {
                    ...stakeholder,
                    type: threatType,
                    displayThreatLevel: threatType === 'actual' ? stakeholder.threat_level : stakeholder.residual_threat_level,
                    id: `${stakeholder.id}_${threatType}`
                };

                const position = this._calculateStakeholderPosition(marker, positionRatio);
                markers.push({
                    ...marker,
                    x: position.x,
                    y: position.y
                });
            });
        });

        return markers;
    }

    _getRiskZone(threatLevel) {
        const level = parseFloat(threatLevel) || 0;
        if (level <= 2) return 'veille';
        if (level <= 5) return 'control';
        if (level <= 10) return 'danger1';
        return 'danger2';
    }

    _calculateStakeholderPosition(stakeholder, positionRatio = 0.5) {
        const categoryInfo = {
            'prestataires': { baseAngle: 135, range: 90 },
            'partenaires': { baseAngle: 315, range: 90 },
            'customers': { baseAngle: 225, range: 90 },
            'personnels': { baseAngle: 45, range: 90 }
        };

        const categoryData = categoryInfo[stakeholder.category] || { baseAngle: 0, range: 90 };
        const baseAngleOffset = (positionRatio - 0.5) * categoryData.range * 0.99;
        const finalAngle = categoryData.baseAngle + baseAngleOffset;

        const maxThreatLevel = 16;
        const maxRadius = 200;
        const minRadius = 15;

        const threatLevel = parseFloat(stakeholder.displayThreatLevel) || 0;
        const normalizedRadius = maxRadius - (threatLevel / maxThreatLevel) * (maxRadius - minRadius);

        const radians = finalAngle * (Math.PI / 180);
        const x = 250 + normalizedRadius * Math.cos(radians);
        const y = 250 + normalizedRadius * Math.sin(radians);

        return { x, y };
    }

    calculateStakeholderX(stakeholder) {
        return stakeholder.x;
    }

    calculateStakeholderY(stakeholder) {
        return stakeholder.y;
    }

    getStakeholderRadius(stakeholder) {
        const sizeRadiusMap = {
            'xs': 4,
            'sm': 6,
            'md': 8,
            'lg': 10
        };
        return sizeRadiusMap[stakeholder.stakeholder_size] || 6;
    }

    getStakeholderColor(stakeholder) {
        const colorMap = {
            'red': '#B5182E',
            'yellow': '#F4AE3E',
            'blue': '#4EA6D8',
            'green': '#46D684'
        };
        return colorMap[stakeholder.color_index] || '#9E9E9E';
    }

    selectStakeholder(stakeholder) {
        this.state.selectedStakeholder = stakeholder;
    }

    showTooltip(event, stakeholder) {
        const containerRect = event.currentTarget.closest('.o_threat_mapping_container').getBoundingClientRect();

        this.tooltip.visible = true;
        this.tooltip.x = event.clientX - containerRect.left + 10;
        this.tooltip.y = event.clientY - containerRect.top - 10;
        this.tooltip.content = this._getTooltipContent(stakeholder);
    }

    hideTooltip() {
        this.tooltip.visible = false;
    }

    _getTooltipContent(stakeholder) {
        const categoryInfo = {
            'prestataires': { name: 'Prestataires', baseAngle: 135 },
            'partenaires': { name: 'Partenaires', baseAngle: 315 },
            'customers': { name: 'Clients', baseAngle: 225 },
            'personnels': { name: 'Personnels', baseAngle: 45 }
        };

        const category = categoryInfo[stakeholder.category] || { name: stakeholder.category, baseAngle: 0 };
        const threatLevel = parseFloat(stakeholder.displayThreatLevel) || 0;
        const riskZone = this._getRiskZone(threatLevel);
        const riskZoneNames = {
            'veille': 'Zone de veille (0-2)',
            'control': 'Zone de contrôle (2-5)',
            'danger1': 'Zone de danger (5-10)',
            'danger2': 'Zone de danger (10-16)'
        };

        const threatType = stakeholder.type === 'actual' ? 'Niveau de menace actuel' : 'Niveau de menace résiduel';

        return {
            name: stakeholder.name,
            category: category.name,
            threatLevel: threatLevel.toFixed(2),
            threatType: threatType,
            exposition: stakeholder.exposition,
            cyberReliability: stakeholder.cyber_reliability,
            riskZone: riskZoneNames[riskZone] || riskZone
        };
    }

    async captureCartography(type) {
        if (typeof html2canvas === 'undefined') {
            this.notification.add('html2canvas library not loaded. Please refresh the page.', {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;
        const elementId = type === 'actual' ? 'actual-cartography' : 'residual-cartography';
        const element = document.getElementById(elementId);

        if (!element) {
            this.notification.add(`Cartography element not found: ${elementId}`, {
                type: 'danger'
            });
            this.state.isCapturing = false;
            return;
        }

        try {
            // Configure html2canvas options for SVG
            const canvas = await html2canvas(element, {
                allowTaint: true,
                useCORS: true,
                scale: 2, // Higher resolution
                logging: false,
                backgroundColor: '#ffffff',
                // Special handling for SVG
                onclone: (clonedDoc) => {
                    // Ensure SVG elements are properly rendered in the clone
                    const svgs = clonedDoc.querySelectorAll('svg');
                    svgs.forEach(svg => {
                        // Set explicit dimensions if not already set
                        if (!svg.getAttribute('width')) {
                            svg.setAttribute('width', '500px');
                        }
                        if (!svg.getAttribute('height')) {
                            svg.setAttribute('height', '500px');
                        }
                    });
                }
            });

            // Convert canvas to base64 image
            const imageData = canvas.toDataURL('image/png');

            // Store the image data
            if (type === 'actual') {
                this.state.actualCartographyImage = imageData;
            } else {
                this.state.residualCartographyImage = imageData;
            }

            // Save the image to the server
            await this.saveCartographyImage(type, imageData);

            this.notification.add(`${type === 'actual' ? 'Actual' : 'Residual'} cartography captured successfully!`, {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing cartography:', error);
            this.notification.add('Failed to capture cartography. Please try again.', {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
        }
    }

    async saveCartographyImage(type, imageData) {
        try {
            // Convert base64 to the format Odoo expects (remove the data:image/png;base64, prefix)
            const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

            // Update the study record with the image
            if (this.state.studyId) {
                await this.orm.write('digiit.ebios_rm.study', [this.state.studyId], {
                    [`${type}_cartography_image`]: base64Data
                });
                console.log(`${type} cartography image saved to study ${this.state.studyId}`);
            }
        } catch (error) {
            console.error('Error saving cartography image:', error);
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
            // Capture actual cartography
            await this.captureCartography('actual');

            // Capture residual cartography
            await this.captureCartography('residual');

            // Capture legend
            const legendElement = document.getElementById('legend-section');
            if (legendElement) {
                const canvas = await html2canvas(legendElement, {
                    allowTaint: true,
                    useCORS: true,
                    scale: 2,
                    logging: false,
                    backgroundColor: '#ffffff'
                });

                const imageData = canvas.toDataURL('image/png');
                const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

                // Save the legend image
                if (this.state.studyId) {
                    await this.orm.write('digiit.ebios_rm.study', [this.state.studyId], {
                        'cartography_legend_image': base64Data
                    });
                    this.state.legendImage = imageData;
                }
            }

            this.notification.add('All cartographies and legend captured successfully!', {
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

EcosystemThreatMap.template = 'digiit_ebios_rm.EcosystemThreatMap';

registry.category("actions").add("digiit_ebios_rm.threat_map", EcosystemThreatMap);

export default EcosystemThreatMap;