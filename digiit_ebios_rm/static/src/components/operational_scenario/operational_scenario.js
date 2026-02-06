/** @odoo-module **/
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

class EbiosOperationalScenario extends Component {
    setup() {
        this.state = useState({
            elementarySteps: [],
            groupedSteps: {},
            phases: [
                { id: 1, name: "CONNAÎTRE", steps: [] },
                { id: 2, name: "RENTRER", steps: [] },
                { id: 3, name: "TROUVER", steps: [] },
                { id: 4, name: "EXPLOITER", steps: [] },
            ],
            connections: [],
            selectedStepId: null,
            currentScenarioName: this.props.action?.context?.scenario_name || "New Scenario",
            expandedGroups: {},
            scenarioId: null,
            strategicScId: null,
            strategicScenarios: [],
            strategicConnections: [],
            selectedScenario: null,
            studyId: null,
            operationalLevels: [],
            selectedConnections: [], // Array of connection IDs
            pendingConnectionSource: null,
            showAddStepModal: false,
            newStepName: '',
            newStepPhase: '',
            newStepDescription: '',
            newStepRiskLevel: 'medium',
            showDeleteConfirmModal: false,
            stepToDelete: null,
            isCapturing: false,
            capturedImage: null,
        });

        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.draggedStep = null;
        this.workspaceRef = useRef("workspace");
        this.connectionStart = null;
        this.workspaceClickHandler = null;

        const params = this.props.action?.params || {};
        const context = this.props.action?.context || {};

        this.state.scenarioId = params.scenario_id || context.scenario_id || context.active_id || null;
        this.state.studyId = params.study_id || context.study_id || null;
        this.state.strategicScId = params.strategic_sc_id || context.strategic_sc_id || null;
        this.state.stakeholderId = params.stakeholder_id || context.stakeholder_id || null;
        this.state.scenarioDetails = context.scenario_details || null;

        const directAttack = params.direct_attack !== undefined ? params.direct_attack : context.direct_attack;
        if (directAttack !== undefined) {
            // Store it if needed
        }

        if (context.scenario_name) {
            this.state.currentScenarioName = context.scenario_name;
        }

        onMounted(async () => {
            if (!this.state.studyId) {
                await this.fetchDefaultStudy();
            }
            await this.fetchElementarySteps();
            await this.fetchStrategicScenarios();
            if (this.state.scenarioId) {
                await this.loadExistingScenario(this.state.scenarioId);
            }
            if (this.state.strategicScId) {
                await this.fetchStrategicScenarioDetails(this.state.strategicScId);
            }
            this.groupStepsByPhase();
            this.initializeUI();

            // Load html2canvas library
            const script = document.createElement('script');
            script.src = '/digiit_ebios_rm/static/src/libs/html2canvas.min.js';
            script.onload = () => {
                console.log('html2canvas loaded');
            };
            document.head.appendChild(script);
        });
    }

    initializeUI() {
        if (!this.workspaceRef?.el) {
            setTimeout(() => this.initializeUI(), 200);
            return;
        }
        this.setupDragAndDrop();
        this.setupSpecialElements();
        this.setupConnectionsCanvas();
        setTimeout(() => this.updateConnections(), 1000);
    }

    // --- Connection Management ---
    setupConnectionsCanvas() {
        const canvas = document.getElementById("connections-canvas");
        if (!canvas) {
            console.warn("Canvas element not found");
            return;
        }
        const ctx = canvas.getContext("2d");
        if (!this.workspaceRef?.el || !ctx) {
            console.warn("Workspace or canvas context not available");
            return;
        }
        canvas.width = this.workspaceRef.el.offsetWidth;
        canvas.height = this.workspaceRef.el.offsetHeight;
        canvas.style.pointerEvents = "none";

        if (this.workspaceClickHandler && this.workspaceRef?.el) {
            this.workspaceRef.el.removeEventListener("click", this.workspaceClickHandler);
        }
        if (this.workspaceRef?.el) {
            this.workspaceClickHandler = (e) => this.handleWorkspaceClick(e);
            this.workspaceRef.el.addEventListener("click", this.workspaceClickHandler);
        }

        window.addEventListener("resize", () => {
            if (this.workspaceRef?.el && canvas) {
                canvas.width = this.workspaceRef.el.offsetWidth;
                canvas.height = this.workspaceRef.el.offsetHeight;
                this.drawConnections(ctx);
            }
        });
    }

    deleteSelectedConnections() {
        if (this.state.selectedConnections.length === 0) {
            this.notification.add(_t("No connections selected. Click a connection line first."), { type: "warning" });
            return;
        }
        this.state.connections = this.state.connections.filter(
            conn => !this.state.selectedConnections.includes(conn.id)
        );
        this.state.selectedConnections = [];
        this.recalculateOperationalLevels();
        this.updateConnections();
    }

    clearConnections() {
        this.state.connections = [];
        this.state.selectedConnections = [];
        this.state.operationalLevels = [];
        this.updateConnections();
    }

    handleWorkspaceClick(e) {
        if (!this.workspaceRef?.el?.contains(e.target)) {
            return;
        }
        if (
            e.target.closest(".step-instance") ||
            e.target.closest(".elementary-step") ||
            e.target.closest(".pending-connection-alert")
        ) {
            return;
        }
        this.handleCanvasClick(e);
    }

    handleCanvasClick(e) {
        const canvas = document.getElementById("connections-canvas");
        if (!canvas) {
            return;
        }
        const rect = canvas.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const clickY = e.clientY - rect.top;

        // Find clicked connection
        const clickedConnection = this.state.connections.find((conn, index) => {
            const route = this.calculateSmartRoute(conn.source, conn.target, index);
            return this.isPointNearRoute(clickX, clickY, route);
        });

        if (clickedConnection) {
            const connId = clickedConnection.id;
            if (this.state.selectedConnections.includes(connId)) {
                this.state.selectedConnections = [];
            } else {
                this.state.selectedConnections = [connId];
            }
        } else {
            this.state.selectedConnections = [];
        }
        this.updateConnections();
    }

    isPointNearRoute(x, y, route) {
        const threshold = 5; // Pixel threshold for click detection
        for (let i = 0; i < route.length - 1; i++) {
            const start = route[i];
            const end = route[i + 1];
            if (start.x === end.x) { // Vertical segment
                if (Math.abs(x - start.x) < threshold &&
                    y >= Math.min(start.y, end.y) &&
                    y <= Math.max(start.y, end.y)) {
                    return true;
                }
            } else { // Horizontal segment
                if (Math.abs(y - start.y) < threshold &&
                    x >= Math.min(start.x, end.x) &&
                    x <= Math.max(start.x, end.x)) {
                    return true;
                }
            }
        }
        return false;
    }

    calculateSmartRoute(sourceId, targetId, connectionIndex) {
        const sourceEl = document.querySelector(`[data-instance-id="${sourceId}"]`);
        const targetEl = document.querySelector(`[data-instance-id="${targetId}"]`);
        if (!sourceEl || !targetEl) return [];

        const canvas = document.getElementById("connections-canvas");
        if (!canvas) return [];
        const canvasRect = canvas.getBoundingClientRect();
        const sourceRect = sourceEl.getBoundingClientRect();
        const targetRect = targetEl.getBoundingClientRect();

        // Determine element types
        const sourceType = this.getElementType(sourceId);
        const targetType = this.getElementType(targetId);

        // Check if both steps are in the same phase (only declared once)
        const sourcePhaseContainer = sourceEl.closest('.phase-container');
        const targetPhaseContainer = targetEl.closest('.phase-container');
        const samePhase = sourcePhaseContainer && targetPhaseContainer &&
                         sourcePhaseContainer === targetPhaseContainer;

        // Connection points
        let from, to;

        // For same-phase step connections: use bottom/top centers
        if (sourceType === 'step' && targetType === 'step' && samePhase) {
            from = {
                x: sourceRect.left + sourceRect.width / 2 - canvasRect.left,
                y: sourceRect.bottom - canvasRect.top
            };
            to = {
                x: targetRect.left + targetRect.width / 2 - canvasRect.left,
                y: targetRect.top - canvasRect.top
            };
        }
        // Special handling for menace (left side) - connect from right edge
        else if (sourceType === 'menace') {
            from = {
                x: sourceRect.right - canvasRect.left,
                y: sourceRect.top + sourceRect.height / 2 - canvasRect.top
            };
            to = {
                x: targetRect.left - canvasRect.left,
                y: targetRect.top + targetRect.height / 2 - canvasRect.top
            };
        }
        // Special handling for actif (right side) - connect to left edge
        else if (targetType === 'actif') {
            from = {
                x: sourceRect.right - canvasRect.left,
                y: sourceRect.top + sourceRect.height / 2 - canvasRect.top
            };
            to = {
                x: targetRect.left - canvasRect.left,
                y: targetRect.top + targetRect.height / 2 - canvasRect.top
            };
        }
        // Default: right edge to left edge (middle)
        else {
            from = {
                x: sourceRect.right - canvasRect.left,
                y: sourceRect.top + sourceRect.height / 2 - canvasRect.top
            };
            to = {
                x: targetRect.left - canvasRect.left,
                y: targetRect.top + targetRect.height / 2 - canvasRect.top
            };
        }

        // Key improvement: group connections by (source → target) pair to avoid overlap
        const pairKey = `${sourceId}|${targetId}`;
        if (!this.connectionOffsets) this.connectionOffsets = {};
        if (!(pairKey in this.connectionOffsets)) {
            this.connectionOffsets[pairKey] = 0;
        }
        const parallelIndex = this.connectionOffsets[pairKey]++;
        // Smart vertical offset for parallel connections
        const SPACING = 20;
        const MAX_OFFSET = 60;
        const offset = (parallelIndex - Math.floor(this.state.connections.filter(c =>
            `${c.source}|${c.target}` === pairKey).length / 2)) * SPACING;
        const clampedOffset = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET, offset));

        const route = [];
        const verticalDistance = Math.abs(from.y - to.y);
        const HORIZONTAL_THRESHOLD = 30; // pixels - if Y positions are within this, draw horizontal

        // For step → step connections in the same phase: simple direct vertical line
        if (sourceType === 'step' && targetType === 'step' && samePhase) {
            route.push({ x: from.x, y: from.y });

            // Optional: small dog-leg in the middle so lines don't overlap perfectly
            const midY = from.y + (to.y - from.y) * 0.5;

            // Go vertical from source
            route.push({ x: from.x, y: midY });

            // Go horizontal in the middle
            route.push({ x: to.x, y: midY });

            // Final segment: vertical (arrow will point up/down)
            route.push({ x: to.x, y: to.y });
        }
        // For step → step connections across phases at similar heights: simple horizontal line
        else if (sourceType === 'step' && targetType === 'step' && verticalDistance < HORIZONTAL_THRESHOLD) {
            route.push({ x: from.x, y: from.y });
            route.push({ x: to.x, y: to.y });
        }
        // For menace → step connections: simpler, more direct routing
        else if (sourceType === 'menace' && targetType === 'step') {
            route.push({ x: from.x, y: from.y });
            route.push({ x: from.x + 30, y: from.y });
            const midY = from.y + (to.y - from.y) * 0.5;
            route.push({ x: from.x + 30, y: midY + clampedOffset });
            route.push({ x: to.x - 30, y: midY + clampedOffset });
            route.push({ x: to.x - 30, y: to.y });
            route.push({ x: to.x, y: to.y });
        }
        // For step → target or target → actif: cleaner routing
        else if (sourceType === 'step' && targetType === 'target') {
            // for step → target
            route.push({ x: from.x, y: from.y });
            route.push({ x: from.x + 30, y: from.y });
            const midY = from.y + (to.y - from.y) * 0.5;
            route.push({ x: from.x + 30, y: midY + clampedOffset });
            route.push({ x: to.x - 30, y: midY + clampedOffset });
            route.push({ x: to.x - 30, y: to.y });
            route.push({ x: to.x, y: to.y });
        }
        // For target → actif: pure horizontal arrow
        else if (sourceType === 'target' && targetType === 'actif') {
            // last two points must share the same Y so the arrow is horizontal
            route.push({ x: from.x, y: from.y });   // start at ER
            route.push({ x: to.x,   y: from.y });   // straight horizontal to Actif
        }
        // Default routing for step → step connections across different phases
        else {
            const midY = from.y + (to.y - from.y) * 0.5;
            route.push({ x: from.x, y: from.y });
            route.push({ x: from.x + 50, y: from.y });
            route.push({ x: from.x + 50, y: midY + clampedOffset });
            const midX = Math.max(from.x + 100, (from.x + to.x) / 2);
            route.push({ x: midX, y: midY + clampedOffset });
            route.push({ x: to.x - 50, y: midY + clampedOffset });
            route.push({ x: to.x - 50, y: to.y });
            route.push({ x: to.x, y: to.y });
        }

        return route;
    }
    getElementType(id) {
        const idStr = String(id);
        if (idStr.startsWith('menace-')) return 'menace';
        if (idStr.startsWith('target-')) return 'target';
        if (idStr.startsWith('actif-')) return 'actif';
        return 'step';
    }

    drawConnections(ctx) {
        if (!ctx?.canvas || !this.workspaceRef?.el) {
            console.warn("Canvas context or workspace not available");
            return;
        }
        ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);

        const levelColors = [
            "#e91e63", // Pink - Level 1
            "#00bcd4", // Cyan - Level 2
            "#2196f3", // Blue - Level 3
            "#ff9800", // Orange - Level 4
            "#9c27b0", // Purple - Level 5
            "#4caf50", // Green - Level 6
            "#f44336", // Red - Level 7
            "#ffeb3b", // Yellow - Level 8
        ];

        this.state.connections.forEach((conn, index) => {
            const sourceEl = document.querySelector(`[data-instance-id="${conn.source}"]`);
            const targetEl = document.querySelector(`[data-instance-id="${conn.target}"]`);
            if (!sourceEl || !targetEl) return;

            const route = this.calculateSmartRoute(conn.source, conn.target, index);
            if (route.length === 0) return;

            ctx.beginPath();
            ctx.moveTo(route[0].x, route[0].y);
            for (let i = 1; i < route.length; i++) {
                ctx.lineTo(route[i].x, route[i].y);
            }

            const colorIndex = (conn.operationalLevel || 1) - 1;
            ctx.strokeStyle = levelColors[colorIndex % levelColors.length];
            ctx.lineWidth = this.state.selectedConnections.includes(conn.id) ? 4 : 2;
            ctx.stroke();

            // Draw arrow
            const endX = route[route.length - 1].x;
            const endY = route[route.length - 1].y;
            const fromX = route[route.length - 2].x;
            const fromY = route[route.length - 2].y;
            this.drawArrow(ctx, endX, endY, fromX, fromY, ctx.strokeStyle);
            if (!targetEl) console.log("Missing target DOM for:", conn.target);
        });
    }

    drawArrow(ctx, toX, toY, fromX, fromY, color) {
        const headLength = 12;

        // Normalize vertical/horizontal last segment
        if (Math.abs(toX - fromX) < 2) {
            fromX = toX; // vertically aligned
        }
        if (Math.abs(toY - fromY) < 2) {
            fromY = toY; // horizontally aligned
        }

        const angle = Math.atan2(toY - fromY, toX - fromX);

        ctx.beginPath();
        ctx.moveTo(toX, toY);
        ctx.lineTo(
            toX - headLength * Math.cos(angle - Math.PI / 6),
            toY - headLength * Math.sin(angle - Math.PI / 6)
        );
        ctx.moveTo(toX, toY);
        ctx.lineTo(
            toX - headLength * Math.cos(angle + Math.PI / 6),
            toY - headLength * Math.sin(angle + Math.PI / 6)
        );
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.stroke();
    }

    selectStep(stepInstanceId, event) {
        if (event) {
            event.stopPropagation();
        }

        if (!this.connectionStart) {
            this.startConnectionFromStep(stepInstanceId);
            return;
        }

        if (this.connectionStart === stepInstanceId) {
            this.cancelPendingConnection();
            this.updateConnections();
            return;
        }

        if (this.validateConnection(this.connectionStart, stepInstanceId)) {
            const existingConnection = this.state.connections.find(
                c => c.source === this.connectionStart && c.target === stepInstanceId
            );
            if (!existingConnection) {
                const newConnection = {
                    id: Date.now(),
                    source: this.connectionStart,
                    target: stepInstanceId,
                    type: "serial",
                    operationalLevel: null // Will be assigned by recalculateOperationalLevels
                };
                this.state.connections.push(newConnection);

                // Recalculate operational levels for all paths
                this.recalculateOperationalLevels();
                this.updateConnections();
            }
            this.cancelPendingConnection();
        } else {
            this.notification.add(
                _t("Invalid connection. Follow the process: Menace → Steps → Target → Actif"),
                { type: "warning" }
            );
            this.cancelPendingConnection();
            this.updateConnections();
        }
    }

    recalculateOperationalLevels() {
        // Find all unique mode opératoires (distinct menace→actif journeys)
        const modeOperatoires = this.findDistinctModeOperatoires();

        // Assign operational levels to each mode
        modeOperatoires.forEach((mode, index) => {
            const level = index + 1;
            mode.connectionIds.forEach(connId => {
                const conn = this.state.connections.find(c => c.id === connId);
                if (conn) {
                    conn.operationalLevel = level;
                }
            });
        });

        // Clear operational levels for connections not in any complete mode
        const allModeConnections = new Set(
            modeOperatoires.flatMap(mode => mode.connectionIds)
        );
        this.state.connections.forEach(conn => {
            if (!allModeConnections.has(conn.id)) {
                conn.operationalLevel = null;
            }
        });
    }

    findDistinctModeOperatoires() {
        // Step 1: Find the menace and target nodes (should be unique in a scenario)
        const menaceNodes = [...new Set(
            this.state.connections
                .map(c => c.source)
                .filter(id => String(id).startsWith('menace-'))
        )];

        const targetNodes = [...new Set(
            this.state.connections
                .map(c => c.target)
                .filter(id => String(id).startsWith('target-'))
        )];

        if (menaceNodes.length === 0 || targetNodes.length === 0) {
            return [];
        }

        // Step 2: Find all paths from menace to target (these are the modes)
        const allModePaths = [];

        menaceNodes.forEach(menaceId => {
            targetNodes.forEach(targetId => {
                const paths = this.findAllPathsBetween(menaceId, targetId);
                paths.forEach(path => {
                    if (path.length > 0) {
                        allModePaths.push({
                            menaceId,
                            targetId,
                            connectionIds: path
                        });
                    }
                });
            });
        });

        // Step 3: Group paths that share connections (convergence/divergence between menace and target)
        const modes = this.groupPathsIntoModes(allModePaths);

        // Step 4: Add the target→actif connection(s) to each mode
        modes.forEach(mode => {
            const targetToActifConnections = this.state.connections
                .filter(c => String(c.source).startsWith('target-') && String(c.target).startsWith('actif-'))
                .map(c => c.id);

            mode.connectionIds.push(...targetToActifConnections);
        });

        return modes;
    }

    findAllPathsBetween(startNode, endNode, visited = new Set(), currentPath = []) {
        // If we reached the target, return the current path
        if (startNode === endNode) {
            return [currentPath];
        }

        const paths = [];
        visited.add(startNode);

        // Find all connections from this node
        const outgoingConnections = this.state.connections.filter(c => c.source === startNode);

        outgoingConnections.forEach(conn => {
            // Skip if target is the destination (we'll handle it above)
            if (!visited.has(conn.target)) {
                const newPath = [...currentPath, conn.id];
                const subPaths = this.findAllPathsBetween(conn.target, endNode, new Set(visited), newPath);
                paths.push(...subPaths);
            }
        });

        return paths;
    }

    groupPathsIntoModes(individualPaths) {
        if (individualPaths.length === 0) return [];

        const modes = [];
        const processed = new Set();

        individualPaths.forEach((path, index) => {
            if (processed.has(index)) return;

            // Start a new mode with this path
            const modeConnections = new Set(path.connectionIds);
            const pathsInMode = [index];
            processed.add(index);

            // Find all other paths that share at least one connection with this mode
            let foundNewPath = true;
            while (foundNewPath) {
                foundNewPath = false;

                individualPaths.forEach((otherPath, otherIndex) => {
                    if (processed.has(otherIndex)) return;

                    // Check if this path shares any connection with current mode
                    const hasSharedConnection = otherPath.connectionIds.some(
                        connId => modeConnections.has(connId)
                    );

                    if (hasSharedConnection) {
                        // Add this path to the mode
                        otherPath.connectionIds.forEach(connId => modeConnections.add(connId));
                        pathsInMode.push(otherIndex);
                        processed.add(otherIndex);
                        foundNewPath = true;
                    }
                });
            }

            // Add this mode
            modes.push({
                menaceId: path.menaceId,
                targetId: path.targetId,
                connectionIds: Array.from(modeConnections),
                pathCount: pathsInMode.length
            });
        });

        return modes;
    }

    startConnectionFromStep(stepInstanceId) {
        this.state.selectedConnections = [];
        this.connectionStart = stepInstanceId;
        this.state.pendingConnectionSource = stepInstanceId;
        this.state.selectedStepId = stepInstanceId;
    }

    cancelPendingConnection() {
        this.connectionStart = null;
        this.state.pendingConnectionSource = null;
        this.state.selectedStepId = null;
    }

    // --- Existing Methods (Modified for Compatibility) ---
    async loadExistingScenario(scenarioId) {
        try {
            if (this.props.action?.context?.scenario_data) {
                const contextData = this.props.action.context.scenario_data;
                if (contextData.phases?.length > 0) {
                    this.state.phases = contextData.phases.map(phase => ({
                        ...phase,
                        steps: phase.steps.map(step => ({
                            ...step,
                            instanceId: step.instanceId
                        })),
                    }));
                }
                if (contextData.connections?.length > 0) {
                    this.state.connections = contextData.connections.map(conn => ({
                        ...conn,
                        operationalLevel: conn.operationalLevel || null
                    }));
                }
                setTimeout(() => this.updateConnections(), 500);
                return;
            }

            const scenario = await this.orm.read(
                'digiit.ebios_rm.operational.scenario',
                [scenarioId],
                ['prefixed_identifier', 'risk_identifier', 'scenario_data', 'strategic_sc_id', 'study_id']            );

            if (!scenario?.length) {
                throw new Error(`Scenario with ID ${scenarioId} not found.`);
            }

            const scenarioData = scenario[0];
            this.state.currentScenarioName = this.props.action?.context?.scenario_name || scenarioData.risk_identifier || scenarioData.prefixed_identifier || "Unnamed Scenario";
            this.state.strategicScId = scenarioData.strategic_sc_id?.[0] || this.state.strategicScId;
            this.state.studyId = scenarioData.study_id?.[0] || this.state.studyId;

            if (scenarioData.scenario_data) {
                const parsedData = JSON.parse(scenarioData.scenario_data);
                if (parsedData.phases?.length > 0) {
                    this.state.phases = parsedData.phases.map(phase => ({
                        ...phase,
                        steps: phase.steps.map(step => ({
                            ...step,
                            instanceId: step.instanceId
                        })),
                    }));
                }
                if (parsedData.connections?.length > 0) {
                    this.state.connections = parsedData.connections.map(conn => ({
                        ...conn,
                        operationalLevel: conn.operationalLevel || null
                    }));
                }
                setTimeout(() => this.updateConnections(), 500);
            }

            if (this.state.strategicScId) {
                await this.fetchStrategicScenarioDetails(this.state.strategicScId);
            }

            this.notification.add(
                _t("Sc. opérationnel chargé avec succès"),
                { type: "success" }
            );
        } catch (error) {
            console.error("Error loading scenario:", error);
            this.notification.add(
                _t("Échec de la récupération des détails du scénario stratégique: ") + error.message,
                { type: "danger" }
            );
        }
    }

    async saveScenario() {
        try {
            const scenarioData = {
                name: this.state.currentScenarioName,
                phases: this.state.phases.map(phase => ({
                    id: phase.id,
                    name: phase.name,
                    steps: phase.steps.map(step => ({
                        id: step.id,
                        name: step.name,
                        instanceId: step.instanceId,
                        elementaryStepId: step.id,
                        position: step.position || { x: 0, y: 0 },
                    }))
                })),
                connections: this.state.connections,
                study_id: this.state.studyId || false,
            };

            if (this.state.strategicScId) {
                scenarioData.strategic_sc_id = this.state.strategicScId;
            }

            if (this.state.scenarioId) {
                scenarioData.id = this.state.scenarioId;
            }

            const scenarioId = await this.orm.call(
                'digiit.ebios_rm.operational.scenario',
                'create_from_builder',
                [scenarioData]
            );

            if (!this.state.scenarioId) {
                this.state.scenarioId = scenarioId;
            }

            const contextScenarioId = this.props.action?.context?.scenario_id || null;
            if (scenarioId && scenarioId !== contextScenarioId) {
                await this.action.doAction({
                    type: 'ir.actions.client',
                    tag: 'digiit_ebios_rm_operational_scenario.view',
                    context: {
                        scenario_id: scenarioId,
                        study_id: this.state.studyId,
                        scenario_name: this.state.currentScenarioName,
                        strategic_sc_id: this.state.strategicScId,
                        direct_attack: this.props.action?.context?.direct_attack || false,
                    },
                });
            }

            this.notification.add(
                _t("Sc. opérationnel sauvegardé avec succés"),
                { type: "success" }
            );

            return scenarioId;
        } catch (error) {
            console.error("Error saving scenario:", error);
            this.notification.add(
                _t("Failed to save operational scenario: ") + error.message,
                { type: "danger" }
            );
            return false;
        }
    }

    // --- Unchanged Methods ---
    async fetchDefaultStudy() {
        try {
            const studies = await this.orm.searchRead('digiit.ebios_rm.study', [], ['id'], { limit: 1 });
            if (studies.length) {
                this.state.studyId = studies[0].id;
            } else {
                this.notification.add(
                    _t("Aucune étude trouvée. Veuillez d'abord créer une étude."),
                    { type: "warning" }
                );
            }
        } catch (error) {
            this.notification.add(
                _t("Échec de la récupération de l'étude par défaut") + error.message,
                { type: "danger" }
            );
        }
    }

    async fetchStrategicScenarios() {
        try {
            const records = await this.orm.searchRead(
                "digiit.ebios_rm.risk.scenario",
                this.state.studyId ? [['study_id', '=', this.state.studyId]] : [],
                ["id", "menace_id", "actif_id", "pp_id", "description", "damage"]
            );
            this.state.strategicScenarios = records.map(record => ({
                ...record,
                displayName: record.description ||
                    `${record.menace_id[1]} → ${record.actif_id[1]}` +
                    (record.pp_id ? ` via ${record.pp_id[1]}` : '')
            }));
        } catch (error) {
            this.notification.add(
                _t("Échec de la récupération des étapes élémentaires:") + error.message,
                { type: "danger" }
            );
        }
    }

    async fetchStrategicScenarioDetails(strategicScId) {
        try {
            if (!strategicScId) {
                this.state.strategicConnections = [];
                return;
            }

            // First check if we already have the scenario details from the context (passed from Python)
            if (this.state.scenarioDetails) {
                let filteredConnections = this.state.scenarioDetails.connections || [];

                // Filter connections based on direct attack setting
                const isDirectAttack = this.props.action?.context?.direct_attack || false;
                filteredConnections = filteredConnections.filter(connection => {
                    const isDirect = connection.scenarios?.[0]?.is_direct_attack || false;
                    return isDirectAttack ? isDirect : !isDirect;
                });

                this.state.strategicConnections = filteredConnections;
                return;
            }

            // If we don't have pre-fetched details, fall back to the existing behavior
            const isDirectAttack = this.props.action?.context?.direct_attack || false;
            const scenarioDetails = await this.orm.call(
                'digiit.ebios_rm.visual.strategic.scenario',
                'get_scenario_details',
                [strategicScId]
            );

            let filteredConnections = scenarioDetails?.connections || [];
            filteredConnections = filteredConnections.filter(connection => {
                const isDirect = connection.scenarios?.[0]?.is_direct_attack || false;
                return isDirectAttack ? isDirect : !isDirect;
            });

            // If we have a specific stakeholder ID for this operational scenario,
            // and we didn't get pre-fetched details, update the stakeholder information
            if (this.state.stakeholderId && filteredConnections.length > 0) {
                // Fetch the specific stakeholder details
                const stakeholder = await this.orm.read(
                    'digiit.ebios_rm.stakeholder',
                    [this.state.stakeholderId],
                    ['name', 'icon']
                );

                if (stakeholder.length > 0) {
                    const stakeholderData = stakeholder[0];

                    // Update the stakeholder in each connection
                    filteredConnections.forEach(connection => {
                        if ('stakeholder' in connection) {
                            connection.stakeholder = {
                                'name': stakeholderData.name || 'Unknown Stakeholder',
                                'logo_url': stakeholderData.icon || '/digiit_ebios_rm/static/src/img/pp_icon.png',
                                'score': connection.stakeholder?.score || 0, // Use the score from the strategic scenario as a fallback
                            };
                        }
                    });
                }
            }

            this.state.strategicConnections = filteredConnections;

            // If no connections found, try to create a basic connection from strategic scenario
            if (!this.state.strategicConnections.length) {
                const scenario = await this.orm.read(
                    'digiit.ebios_rm.visual.strategic.scenario',
                    [strategicScId],
                    ['risk_source_id', 'dreaded_event_id', 'study_id', 'stakeholder_ids', 'damage']
                );

                if (scenario?.length) {
                    const record = scenario[0];
                    const menace = await this.orm.read(
                        'digiit.ebios_rm.risk.menace',
                        [record.risk_source_id?.source_of_risk_id?.id],
                        ['name', 'er', 'objectif', 'icon', 'gravity_level', 'color']
                    );

                    const actif = await this.orm.read(
                        'digiit.ebios_rm.risk.actif',
                        [record.study_id?.company_id?.id],
                        ['name', 'icon']
                    );

                    // Use the specific stakeholder if available, otherwise use the first from strategic scenario
                    let stakeholderInfo = null;
                    if (this.state.stakeholderId) {
                        const specificStakeholder = await this.orm.read(
                            'digiit.ebios_rm.stakeholder',
                            [this.state.stakeholderId],
                            ['name', 'icon']
                        );
                        if (specificStakeholder.length > 0) {
                            stakeholderInfo = {
                                'name': specificStakeholder[0].name || 'Unknown Stakeholder',
                                'logo_url': specificStakeholder[0].icon || '/digiit_ebios_rm/static/src/img/pp_icon.png',
                                'score': 0, // In this fallback case, we don't have the score, but the main path above will work
                            };
                        }
                    } else if (record.stakeholder_ids?.length > 0) {
                        stakeholderInfo = {
                            'name': record.stakeholder_ids[0]?.name || 'Unknown Stakeholder',
                            'logo_url': record.stakeholder_ids[0]?.icon || '/digiit_ebios_rm/static/src/img/pp_icon.png',
                            'score': 0,
                        };
                    }

                    const isDirect = !record.stakeholder_ids?.length || this.state.stakeholderId === null;

                    if ((isDirectAttack && isDirect) || (!isDirectAttack && !isDirect)) {
                        this.state.strategicConnections = [{
                            id: `connection-${strategicScId}`,
                            threat: {
                                name: menace?.[0]?.name || 'Unknown Threat',
                                er: menace?.[0]?.er || record.dreaded_event_id?.name,
                                icon_url: menace?.[0]?.icon || '/digiit_ebios_rm/static/src/img/threat_icon.png',
                                objectif: menace?.[0]?.objectif || 'Risk scenario',
                                gravity_level: menace?.[0]?.gravity_level || 'Medium',
                                color: menace?.[0]?.color || '#FFA500',
                            },
                            asset: {
                                name: actif?.[0]?.name || 'Unknown Asset',
                                icon_url: actif?.[0]?.icon || '/digiit_ebios_rm/static/src/img/asset_icon.png',
                            },
                            objectif: record.damage || 'Risk scenario',
                            gravity_level: menace?.[0]?.gravity_level || 'Medium',
                            gravity_color: menace?.[0]?.color || '#FFA500',
                            scenarios: [{
                                id: strategicScId,
                                number: strategicScId,
                                description: record.damage || 'No description',
                                damage: record.damage,
                                is_direct_attack: isDirect,
                            }],
                            ...(stakeholderInfo && { stakeholder: stakeholderInfo })
                        }];
                    }
                }
            }
        } catch (error) {
            console.error("Error fetching strategic scenario details:", error);
            this.notification.add(
                _t("Failed to fetch strategic scenario details: ") + error.message,
                { type: "danger" }
            );
            this.state.strategicConnections = [];
        }
    }

    getSelectedStrategicScenarioConnection() {
        return this.state.strategicConnections;
    }

    showScenarioDetails(scenario) {
        this.state.selectedScenario = scenario;
    }

    hideScenarioDetails() {
        this.state.selectedScenario = null;
    }

    async fetchElementarySteps() {
        try {
            const records = await this.orm.searchRead(
                "digiit.ebios_rm.elementary.step",
                [],
                ["id", "name", "phase"]
            );
            this.state.elementarySteps = records.map(record => ({
                id: record.id,
                name: record.name,
                phase: record.phase || null,
                phaseName: this.getPhaseNameFromKey(record.phase),
            }));
            const phases = [...new Set(this.state.elementarySteps.map(step => step.phase))];
            phases.forEach(phase => {
                if (phase) {
                    this.state.expandedGroups[phase] = true;
                }
            });
        } catch (error) {
            this.notification.add(
                _t("Failed to fetch elementary steps: ") + error.message,
                { type: "danger" }
            );
        }
    }

    getPhaseNameFromKey(phaseKey) {
        const phaseMapping = {
            'connaitre': 'CONNAÎTRE',
            'rentrer': 'RENTRER',
            'trouver': 'TROUVER',
            'exploiter': 'EXPLOITER'
        };
        return phaseKey ? phaseMapping[phaseKey] : 'Other';
    }

    groupStepsByPhase() {
        const grouped = {};
        const phaseOrder = ['connaitre', 'rentrer', 'trouver', 'exploiter'];
        phaseOrder.forEach(phase => {
            grouped[phase] = [];
        });
        grouped['none'] = [];
        this.state.elementarySteps.forEach(step => {
            if (step.phase && grouped[step.phase]) {
                grouped[step.phase].push(step);
            } else {
                grouped['none'].push(step);
            }
        });
        this.state.groupedSteps = grouped;
    }

    togglePhaseGroup(phaseKey) {
        this.state.expandedGroups[phaseKey] = !this.state.expandedGroups[phaseKey];
    }

    setupDragAndDrop() {
        if (!this.workspaceRef?.el) {
            console.warn("Workspace not available, retrying...");
            setTimeout(() => this.setupDragAndDrop(), 300);
            return;
        }
        const workspace = this.workspaceRef.el;
        document.addEventListener("dragstart", (e) => {
            const target = e.target;
            if (target?.classList?.contains("elementary-step")) {
                const stepId = parseInt(target.dataset.id);
                this.draggedStep = this.state.elementarySteps.find(s => s.id === stepId);
                e.dataTransfer.setData("text/plain", stepId);
                e.dataTransfer.effectAllowed = "copy";
            }
        });
        const phaseContainers = workspace.querySelectorAll(".phase-container");
        if (phaseContainers.length === 0) {
            console.warn("Phase containers not found, retrying...");
            setTimeout(() => this.setupDragAndDrop(), 300);
            return;
        }
        phaseContainers.forEach(container => {
            container.addEventListener("dragover", (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
            });
            container.addEventListener("drop", (e) => {
                e.preventDefault();
                if (!this.draggedStep) return;
                const phaseId = parseInt(container.dataset.phaseId);
                const phase = this.state.phases.find(p => p.id === phaseId);
                if (phase) {
                    const containerRect = container.getBoundingClientRect();
                    const x = e.clientX - containerRect.left;
                    const y = e.clientY - containerRect.top;
                    const stepCopy = {
                        ...this.draggedStep,
                        instanceId: Date.now(),
                        phaseId,
                        position: { x, y }
                    };
                    phase.steps.push(stepCopy);
                    this.state.phases = [...this.state.phases];
                    this.draggedStep = null;
                    this.updateConnections();
                }
            });
        });
        workspace.addEventListener("mousedown", (e) => {
            if (e.target.closest(".step-instance")) {
                const stepEl = e.target.closest(".step-instance");
                const instanceId = stepEl.dataset.instanceId;
                const phaseId = parseInt(stepEl.closest(".phase-container").dataset.phaseId);
                const phase = this.state.phases.find(p => p.id === phaseId);
                if (!phase) return;
                const stepIndex = phase.steps.findIndex(s => s.instanceId == instanceId);
                if (stepIndex === -1) return;
                const step = phase.steps[stepIndex];
                let isDragging = false;
                let startX, startY;
                let originalX = step.position?.x || 0;
                let originalY = step.position?.y || 0;
                const onMouseMove = (moveEvent) => {
                    if (!isDragging) {
                        const deltaX = moveEvent.clientX - startX;
                        const deltaY = moveEvent.clientY - startY;
                        if (Math.abs(deltaX) < 5 && Math.abs(deltaY) < 5) return;
                        isDragging = true;
                    }
                    const containerRect = stepEl.closest(".phase-container").getBoundingClientRect();
                    const newX = originalX + (moveEvent.clientX - startX);
                    const newY = originalY + (moveEvent.clientY - startY);
                    stepEl.style.position = "absolute";
                    stepEl.style.left = `${newX}px`;
                    stepEl.style.top = `${newY}px`;
                    moveEvent.preventDefault();
                    this.updateConnections();
                };
                const onMouseUp = () => {
                    if (isDragging) {
                        const finalX = parseFloat(stepEl.style.left);
                        const finalY = parseFloat(stepEl.style.top);
                        phase.steps[stepIndex].position = { x: finalX, y: finalY };
                        this.state.phases = [...this.state.phases];
                        this.updateConnections();
                    }
                    document.removeEventListener("mousemove", onMouseMove);
                    document.removeEventListener("mouseup", onMouseUp);
                };
                startX = e.clientX;
                startY = e.clientY;
                document.addEventListener("mousemove", onMouseMove);
                document.addEventListener("mouseup", onMouseUp);
            }
        });
    }

    setupSpecialElements() {
        if (!this.workspaceRef?.el) {
            console.warn("Workspace not available for special elements");
            return;
        }
        const workspace = this.workspaceRef.el;
        const menaceElements = workspace.querySelectorAll('[data-instance-id^="menace-"]');
        menaceElements.forEach(el => {
            el.addEventListener("click", (e) => this.selectStep(el.dataset.instanceId, e));
        });
        const targetElement = workspace.querySelector('[data-instance-id^="target-"]');
        if (targetElement) {
            targetElement.addEventListener("click", (e) => this.selectStep(targetElement.dataset.instanceId, e));
        }
        const actifElements = workspace.querySelectorAll('[data-instance-id^="actif-"]');
        actifElements.forEach(el => {
            el.addEventListener("click", (e) => this.selectStep(el.dataset.instanceId, e));
        });
    }

    validateConnection(sourceId, targetId) {
        const getElementType = (id) => {
            const idStr = String(id);
            if (idStr.startsWith('menace-')) return 'menace';
            if (idStr.startsWith('target-')) return 'target';
            if (idStr.startsWith('actif-')) return 'actif';
            return 'step';
        };
        const sourceType = getElementType(sourceId);
        const targetType = getElementType(targetId);
        if (sourceType === 'menace' && targetType === 'step') {
            const phase = this.findPhaseForStep(targetId);
            return phase && phase.id === 1;
        }
        if (sourceType === 'step' && targetType === 'step') {
            return true;
        }
        if (sourceType === 'step' && targetType === 'target') {
            const phase = this.findPhaseForStep(sourceId);
            return phase && phase.id === 4;
        }
        if (sourceType === 'target' && targetType === 'actif') {
            return true;
        }
        return false;
    }

    findPhaseForStep(stepInstanceId) {
        for (const phase of this.state.phases) {
            const step = phase.steps.find(s => s.instanceId == stepInstanceId);
            if (step) return phase;
        }
        return null;
    }

    removeStep(phaseId, stepInstanceId, event) {
        if (event) {
            event.stopPropagation();
        }
        const phase = this.state.phases.find(p => p.id === phaseId);
        if (!phase) return;
        const stepIndex = phase.steps.findIndex(s => s.instanceId === stepInstanceId);
        if (stepIndex !== -1) {
            phase.steps = phase.steps.filter(s => s.instanceId !== stepInstanceId);
        }
        this.state.connections = this.state.connections.filter(
            c => c.source !== stepInstanceId && c.target !== stepInstanceId
        );
        if (this.connectionStart === stepInstanceId) {
            this.cancelPendingConnection();
        }
        if (this.state.selectedStepId === stepInstanceId) {
            this.state.selectedStepId = null;
        }
        this.state.phases = [...this.state.phases];
        this.recalculateOperationalLevels();
        this.updateConnections();
    }

    updateConnections() {
        const canvas = document.getElementById("connections-canvas");
        if (!canvas) {
            console.warn("Canvas not found, retrying...");
            setTimeout(() => {
                const retryCanvas = document.getElementById("connections-canvas");
                if (retryCanvas) {
                    const ctx = retryCanvas.getContext("2d");
                    if (ctx) {
                        this.drawConnections(ctx);
                    }
                }
            }, 500);
            return;
        }
        const ctx = canvas.getContext("2d");
        if (ctx) {
            if (this.workspaceRef?.el) {
                canvas.width = this.workspaceRef.el.offsetWidth;
                canvas.height = this.workspaceRef.el.offsetHeight;
            }
            this.drawConnections(ctx);
        } else {
            console.warn("Canvas context not available");
        }
    }

    openAddStepModal() {
        this.state.showAddStepModal = true;
        this.state.newStepName = '';
        this.state.newStepPhase = '';
        this.state.newStepDescription = '';
        this.state.newStepRiskLevel = 'medium';
    }

    closeAddStepModal() {
        this.state.showAddStepModal = false;
        this.state.newStepName = '';
        this.state.newStepPhase = '';
        this.state.newStepDescription = '';
        this.state.newStepRiskLevel = 'medium';
    }

    async createElementaryStep() {
        try {
            // Validate input
            if (!this.state.newStepName || !this.state.newStepName.trim()) {
                this.notification.add(_t("Step name is required"), { type: "warning" });
                return;
            }

            if (!this.state.newStepPhase) {
                this.notification.add(_t("Please select a phase"), { type: "warning" });
                return;
            }

            // Create the elementary step - returns array of IDs
            const newStepIds = await this.orm.create(
                'digiit.ebios_rm.elementary.step',
                [{
                    name: this.state.newStepName.trim(),
                    phase: this.state.newStepPhase,
                    description: this.state.newStepDescription || false,
                    risk_level: this.state.newStepRiskLevel,
                }]
            );

            // Get the ID (handle both array and single value)
            const newStepId = Array.isArray(newStepIds) ? newStepIds[0] : newStepIds;

            // Create step data directly without extra read
            const stepData = {
                id: newStepId,
                name: this.state.newStepName.trim(),
                phase: this.state.newStepPhase,
                phaseName: this.getPhaseNameFromKey(this.state.newStepPhase),
            };

            // Add to elementarySteps array
            this.state.elementarySteps.push(stepData);

            // Regroup steps
            this.groupStepsByPhase();

            // Expand the group where the step was added
            this.state.expandedGroups[this.state.newStepPhase] = true;

            // Show success notification
            this.notification.add(
                _t("Elementary step created successfully"),
                { type: "success" }
            );

            // Close the modal
            this.closeAddStepModal();

        } catch (error) {
            console.error("Error creating elementary step:", error);
            this.notification.add(
                _t("Failed to create elementary step: ") + error.message,
                { type: "danger" }
            );
        }
    }

    confirmDeleteElementaryStep(stepId, stepName, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }

        this.state.stepToDelete = {
            id: stepId,
            name: stepName
        };
        this.state.showDeleteConfirmModal = true;
    }

    closeDeleteConfirmModal() {
        this.state.showDeleteConfirmModal = false;
        this.state.stepToDelete = null;
    }

    async deleteElementaryStep() {
        if (!this.state.stepToDelete) {
            return;
        }

        try {
            const stepId = this.state.stepToDelete.id;

            await this.orm.unlink('digiit.ebios_rm.elementary.step', [stepId]);

            this.state.elementarySteps = this.state.elementarySteps.filter(
                step => step.id !== stepId
            );

            this.groupStepsByPhase();

            this.state.phases.forEach(phase => {
                const stepsToRemove = phase.steps.filter(step => step.id === stepId);
                stepsToRemove.forEach(step => {
                    this.state.connections = this.state.connections.filter(
                        c => c.source !== step.instanceId && c.target !== step.instanceId
                    );
                });
                phase.steps = phase.steps.filter(step => step.id !== stepId);
            });

            this.recalculateOperationalLevels();
            this.updateConnections();

            this.notification.add(
                _t("Elementary step deleted successfully"),
                { type: "success" }
            );

            this.closeDeleteConfirmModal();

        } catch (error) {
            console.error("Error deleting elementary step:", error);
            this.notification.add(
                _t("Failed to delete elementary step: ") + error.message,
                { type: "danger" }
            );
            this.closeDeleteConfirmModal();
        }
    }

    async captureOperationalScenario() {
        if (typeof html2canvas === 'undefined') {
            this.notification.add(_t('html2canvas library not loaded. Please refresh the page.'), {
                type: 'danger'
            });
            return;
        }

        this.state.isCapturing = true;

        try {
            // Target the cyberkill-chain-container which has all the content
            const element = document.querySelector('.cyberkill-chain-container');
            const connectionsCanvas = document.getElementById('connections-canvas');

            if (!element) {
                this.notification.add(_t('Cyber kill chain container not found'), {
                    type: 'danger'
                });
                this.state.isCapturing = false;
                return;
            }

            // Get the workspace parent for scroll handling
            const workspace = this.workspaceRef?.el || document.querySelector('.workspace');

            // Store original scroll position
            const originalScrollLeft = workspace ? workspace.scrollLeft : 0;
            const originalScrollTop = workspace ? workspace.scrollTop : 0;

            // Scroll to show all content
            if (workspace) {
                workspace.scrollLeft = 0;
                workspace.scrollTop = 0;
            }

            // Wait for scroll and render
            await new Promise(resolve => setTimeout(resolve, 300));

            // Capture the main content WITHOUT the canvas
            const mainCanvas = await html2canvas(element, {
                allowTaint: true,
                useCORS: true,
                scale: 3,
                logging: false,
                backgroundColor: '#ffffff',
                scrollX: 0,
                scrollY: 0,
                ignoreElements: (el) => {
                    // Ignore the connections canvas as we'll overlay it separately
                    return el.id === 'connections-canvas';
                }
            });

            // Create a new canvas to combine both
            const finalCanvas = document.createElement('canvas');
            finalCanvas.width = mainCanvas.width;
            finalCanvas.height = mainCanvas.height;
            const finalCtx = finalCanvas.getContext('2d');

            // Draw the main content
            finalCtx.drawImage(mainCanvas, 0, 0);

            // Draw the connections on top
            if (connectionsCanvas) {
                // Get the position of the connections canvas relative to the captured element
                const elementRect = element.getBoundingClientRect();
                const canvasRect = connectionsCanvas.getBoundingClientRect();

                const offsetX = (canvasRect.left - elementRect.left) * 3; // multiply by scale
                const offsetY = (canvasRect.top - elementRect.top) * 3;

                // Draw the connections canvas onto the final canvas
                finalCtx.drawImage(connectionsCanvas, offsetX, offsetY, connectionsCanvas.width * 3, connectionsCanvas.height * 3);
            }

            // Restore original scroll position
            if (workspace) {
                workspace.scrollLeft = originalScrollLeft;
                workspace.scrollTop = originalScrollTop;
            }

            const imageData = finalCanvas.toDataURL('image/png');

            // Store the image data in state
            this.state.capturedImage = imageData;

            await this.saveOperationalScenarioImage(imageData);

            this.notification.add(_t('Operational scenario captured successfully!'), {
                type: 'success'
            });
        } catch (error) {
            console.error('Error capturing operational scenario:', error);
            this.notification.add(_t('Failed to capture operational scenario. Please try again.'), {
                type: 'danger'
            });
        } finally {
            this.state.isCapturing = false;
        }
    }

    async saveOperationalScenarioImage(imageData) {
        try {
            const base64Data = imageData.replace(/^data:image\/png;base64,/, '');

            if (this.state.scenarioId) {
                await this.orm.write('digiit.ebios_rm.operational.scenario', [this.state.scenarioId], {
                    'scenario_image': base64Data
                });
                console.log(`Operational scenario image saved to scenario ${this.state.scenarioId}`);
            }
        } catch (error) {
            console.error('Error saving operational scenario image:', error);
            throw error;
        }
    }

    goBack() {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            if (this.state.studyId) {
                this.actionService.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'digiit.ebios_rm.study',
                    res_id: this.state.studyId,
                    views: [[false, 'form']],
                    target: 'current',
                    mode: 'readonly',
                    clear_breadcrumb: true
                });
            } else {
                this.actionService.doAction({
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

EbiosOperationalScenario.template = 'digiit_ebios_rm_operational_scenario.Builder';
registry.category("actions").add("digiit_ebios_rm_operational_scenario.view", EbiosOperationalScenario);
export default EbiosOperationalScenario;