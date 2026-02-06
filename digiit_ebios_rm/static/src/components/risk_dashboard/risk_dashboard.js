/** @odoo-module **/

import { Component, useState, onWillStart, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class RiskDashboard extends Component {
    setup() {
        this.state = useState({
            threats: [],
            assets: [],
            pps: [],
            connections: [], // Relations unique menace-actif
            scenarios: [],   // Tous les scénarios
            selectedScenario: null,
            isCapturing: false,
            capturedImages: [], // Array to store all captured images
        });


        this.study_id = this.props.action.context.study_id;

        // Service pour les appels RPC
        this.orm = useService("orm");
        this.action = useService('action');
        this.notification = useService('notification');

        onWillStart(async () => {
            await this.loadDashboardData();
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

    async loadDashboardData() {
        try {
            // This ensures all transient models (like 'digiit.ebios_rm.risk.pp') have the latest scores
            console.log("Preparing dashboard data...");
            await this.orm.call('digiit.ebios_rm.study', 'prepare_dashboard_data', [this.study_id]);
            console.log("Dashboard data prepared. Now loading data for the view...");

            // Récupérer les menaces

            const threats = await this.orm.searchRead(
                "digiit.ebios_rm.risk.menace",
                [],
                ["name", "objectif", "gravity_level", "er", "color", "icon"]
            ) || [];

            for (const threat of threats) {
                if (threat && threat.icon) {
                    // use Odoo's web/image route
                    threat.icon_url = `/web/image/digiit.ebios_rm.risk.menace/${threat.id}/icon`;
                } else if (threat) {
                    threat.icon_url = '/digiit_ebios_rm/static/src/img/threat_icon.png';
                }
            }

            // Récupérer les actifs

            const assets = await this.orm.searchRead(
                "digiit.ebios_rm.risk.actif",
                [],
                ["name", "icon"]
            ) || [];

            // Traitement des icônes des actifs
            for (const asset of assets) {
                if (asset && asset.icon) {
                    asset.icon_url = `data:image/png;base64,${asset.icon}`;
                } else if (asset) {
                    asset.icon_url = '/digiit_ebios_rm/static/src/img/asset_icon.png';
                }
            }

            // Récupérer les parties prenantes
            // The scores for these PPs will now be correct because of the prepare_dashboard_data call
            const pps = await this.orm.searchRead(
                "digiit.ebios_rm.risk.pp",
                [],
                ["name", "logo", "score"]
            ) || [];

            // Traitement des logos des parties prenantes
            for (const pp of pps) {
                if (pp && pp.logo) {
                    pp.logo_url = `data:image/png;base64,${pp.logo}`;
                } else if (pp) {
                    pp.logo_url = '/digiit_ebios_rm/static/src/img/pp_icon.png';
                }
            }

            // Récupérer les scénarios

            const scenarios = await this.orm.searchRead(
                "digiit.ebios_rm.risk.scenario",
                [['study_id', '=', this.study_id]],
                ["menace_id", "actif_id", "pp_id", "is_direct_attack", "description", "damage"]
            ) || [];

            // Traiter les scénarios et créer des connections uniques
            const connections = [];
            const connectionMap = new Map();

            // Traiter chaque scénario
            for (let i = 0; i < scenarios.length; i++) {
                const scenario = scenarios[i];
                if (!scenario) continue;

                scenario.number = i + 1;

                const menaceId = scenario.menace_id && scenario.menace_id.length ? scenario.menace_id[0] : null;
                const actifId = scenario.actif_id && scenario.actif_id.length ? scenario.actif_id[0] : null;
                const ppId = scenario.pp_id && scenario.pp_id.length ? scenario.pp_id[0] : null;

                // Skip scenarios with missing required data
                if (!menaceId || !actifId) {
                    console.warn(`Skipping scenario #${scenario.number} due to missing threat or asset IDs`);
                    continue;
                }

                // Références aux objets complets avec vérifications
                scenario.threat = threats.find(t => t && t.id === menaceId) || null;
                scenario.asset = assets.find(a => a && a.id === actifId) || null;
                scenario.pp = ppId ? pps.find(p => p && p.id === ppId) || null : null;

                // Skip scenarios with missing object references
                if (!scenario.threat || !scenario.asset) {
                    console.warn(`Skipping scenario #${scenario.number} due to missing threat or asset objects`);
                    continue;
                }

                // La clé de connection est formée par la combinaison menace-actif
                const connectionKey = `${menaceId}-${actifId}`;

                // Si c'est une nouvelle connection, l'ajouter à la liste
                if (!connectionMap.has(connectionKey)) {
                    const connection = {
                        id: connectionKey,
                        threat: scenario.threat,
                        asset: scenario.asset,
                        scenarios: [],
                        // Ajout de l'objectif visé depuis la menace
                        objectif: scenario.threat.objectif || "Objectif non spécifié",
                        // Calculer la gravité (placeholder)
                        gravity_level: scenario.threat.gravity_level,
                        gravity_color: scenario.threat.color ? scenario.threat.color : "green"
                    };
                    connectionMap.set(connectionKey, connection);
                    connections.push(connection);
                }

                // Ajouter ce scénario à la connection correspondante
                const connection = connectionMap.get(connectionKey);
                connection.scenarios.push(scenario);
            }

            // Sort scenarios within each connection
            connections.forEach(connection => {
                // Sort scenarios by is_direct_attack first (direct attacks first, then indirect)
                connection.scenarios.sort((a, b) => {
                    // First sort by attack type
                    if (a.is_direct_attack !== b.is_direct_attack) {
                        return a.is_direct_attack ? -1 : 1;
                    }
                    // Then by scenario number
                    return a.number - b.number;
                });

                // Severity value - just default 0
                let maxThreatLevel = 0;


                connection.gravity_level = maxThreatLevel;

                // Determine color based on gravity level
                // if (maxThreatLevel > 2) {
                //     connection.gravity_color = "red";
                // } else if (maxThreatLevel >= 1.5) {
                //     connection.gravity_color = "orange";
                // } else {
                //     connection.gravity_color = "green";
                // }
            });

            // Mettre à jour l'état
            this.state.threats = threats;
            this.state.assets = assets;
            this.state.pps = pps;
            this.state.scenarios = scenarios;
            this.state.connections = connections;
        } catch (error) {
            console.error("Error loading dashboard data:", error);
            this.notification.add("Failed to load dashboard data. Please try again.", { type: "danger" });
        }
    }

    showScenarioDetails(scenario) {
        if (scenario) {
            this.state.selectedScenario = scenario;
        }
    }

    hideScenarioDetails() {
        this.state.selectedScenario = null;
    }

    async captureAllConnections() {
        if (typeof html2canvas === 'undefined') {
            this.notification.add('html2canvas library not loaded. Please refresh the page.', {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;
        this.state.capturedImages = [];

        try {
            const capturedData = [];

            // Capture each connection individually
            for (let i = 0; i < this.state.connections.length; i++) {
                const connection = this.state.connections[i];
                const elementId = `connection-${connection.id}`;
                const element = document.getElementById(elementId);

                if (!element) {
                    console.warn(`Connection element not found: ${elementId}`);
                    continue;
                }

                const canvas = await html2canvas(element, {
                    allowTaint: true,
                    useCORS: true,
                    scale: 2,
                    logging: false,
                    backgroundColor: '#ffffff',
                    width: element.scrollWidth,
                    height: element.scrollHeight,
                    windowWidth: element.scrollWidth,
                    windowHeight: element.scrollHeight,
                });

                const imageData = canvas.toDataURL('image/png');

                // Store in state
                this.state.capturedImages.push({
                    connectionId: connection.id,
                    imageData: imageData,
                    index: i
                });

                capturedData.push({
                    connectionId: connection.id,
                    imageData: imageData,
                    index: i
                });
            }

            // Save all captured images to the backend
            await this.saveAllConnectionImages(capturedData);

            this.notification.add(`Successfully captured ${capturedData.length} strategic scenario diagrams!`, {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing connections:', error);
            this.notification.add('Failed to capture diagrams. Please try again.', {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
        }
    }

    async saveAllConnectionImages(capturedData) {
        try {
            // Prepare the data for saving
            const imagesData = capturedData.map(item => {
                const base64Data = item.imageData.replace(/^data:image\/png;base64,/, '');
                return {
                    index: item.index,
                    connectionId: item.connectionId,
                    imageData: base64Data
                };
            });

            if (this.study_id) {
                // Save all images as a JSON field or individual fields
                await this.orm.write('digiit.ebios_rm.study', [this.study_id], {
                    'strategic_scenario_images': JSON.stringify(imagesData)
                });
                console.log(`${imagesData.length} strategic scenario images saved to study ${this.study_id}`);
            }
        } catch (error) {
            console.error('Error saving connection images:', error);
            throw error;
        }
    }

    goBack() {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            if (this.study_id) {
                this.action.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'digiit.ebios_rm.study',
                    res_id: this.study_id,
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

// Définir le template
RiskDashboard.template = 'digiit_ebios_rm.RiskDashboard';

// Enregistrer le composant
registry.category("actions").add("risk_dashboard_ebios_rm.dashboard", RiskDashboard);

export default RiskDashboard;