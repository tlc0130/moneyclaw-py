import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

let scene, camera, renderer, controls;
let starMesh, gridHelper;
const strategyNodes = {}; // Map: strategy_name -> THREE.Group
const strategyGroup = new THREE.Group();

export function initScene() {
    const canvas = document.querySelector('#world');

    // Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050510); // Very dark blue/black
    scene.fog = new THREE.FogExp2(0x050510, 0.02);

    // Camera
    camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.set(0, 5, 10);
    camera.lookAt(0, 0, 0);

    // Renderer
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(window.devicePixelRatio);

    // Lights
    const ambientLight = new THREE.AmbientLight(0x22d3ee, 0.5); // Cyan ambient
    scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0xffffff, 1);
    pointLight.position.set(10, 10, 10);
    scene.add(pointLight);

    // Controls
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2; // Don't go below ground

    // Objects
    createEnvironment();
    createCentralCore();
    scene.add(strategyGroup);

    // Resize Handler
    window.addEventListener('resize', onWindowResize);
}

function createEnvironment() {
    // 1. Cyberpunk Grid
    const gridSize = 100;
    const gridDivisions = 50;
    gridHelper = new THREE.GridHelper(gridSize, gridDivisions, 0x06b6d4, 0x164e63); // Cyan & Dark Cyan
    gridHelper.position.y = -2;
    scene.add(gridHelper);

    // 2. Starfield
    const starsGeometry = new THREE.BufferGeometry();
    const starsCount = 2000;
    const posArray = new Float32Array(starsCount * 3);

    for (let i = 0; i < starsCount * 3; i++) {
        posArray[i] = (Math.random() - 0.5) * 100;
    }

    starsGeometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));
    const starsMaterial = new THREE.PointsMaterial({
        size: 0.1,
        color: 0xffffff,
        transparent: true,
        opacity: 0.8,
    });

    starMesh = new THREE.Points(starsGeometry, starsMaterial);
    scene.add(starMesh);
}

function createCentralCore() {
    // A glowing core representing the Agent Brain
    const geometry = new THREE.IcosahedronGeometry(1, 1);
    const material = new THREE.MeshBasicMaterial({ color: 0x22d3ee, wireframe: true });
    const core = new THREE.Mesh(geometry, material);
    scene.add(core);

    // Inner glow
    const light = new THREE.PointLight(0x22d3ee, 2, 20);
    core.add(light);

    // Animate core pulsing (stored on userData for the loop)
    core.userData = { pulseSpeed: 0.02, offset: 0 };
    scene.userData.core = core;
}

export function updateStrategiesInScene(strategies) {
    if (!scene) return;

    // strategies is list of dicts: {name, enabled, risk_level, ...}
    const currentNames = new Set(strategies.map(s => s.name));

    // 1. Remove old nodes
    for (const name in strategyNodes) {
        if (!currentNames.has(name)) {
            strategyGroup.remove(strategyNodes[name]);
            delete strategyNodes[name];
        }
    }

    // 2. Add/Update nodes
    const radius = 6;
    strategies.forEach((s, index) => {
        const angle = (index / strategies.length) * Math.PI * 2;
        const x = Math.cos(angle) * radius;
        const z = Math.sin(angle) * radius;

        let node = strategyNodes[s.name];

        if (!node) {
            // Create new node
            node = createStrategyNode(s);
            strategyNodes[s.name] = node;
            strategyGroup.add(node);
        }

        // Update Position (could animate this transition)
        // Lerp to new position would be better, but direct set for now
        node.userData.targetPos = { x, z };
        if (!node.userData.initialized) {
            node.position.set(x, 0, z);
            node.userData.initialized = true;
        }

        // Update Visuals based on state
        const color = s.enabled ? 0x22c55e : 0xff3333; // Green / Red
        const mat = node.userData.material;
        const light = node.userData.light;

        if (mat) mat.color.setHex(color);
        if (mat) mat.emissive.setHex(color);
        if (light) light.color.setHex(color);
    });
}

function createStrategyNode(strategy) {
    const group = new THREE.Group();

    // Visual Mesh
    const geometry = new THREE.OctahedronGeometry(0.5);
    const material = new THREE.MeshStandardMaterial({
        color: 0x888888,
        roughness: 0.2,
        metalness: 0.8,
        emissive: 0x222222,
        emissiveIntensity: 0.5
    });
    const mesh = new THREE.Mesh(geometry, material);
    group.add(mesh);
    group.userData.material = material;

    // Point Light
    const light = new THREE.PointLight(0xffffff, 1, 5);
    group.add(light);
    group.userData.light = light;

    // Orbit Ring
    const ringGeo = new THREE.TorusGeometry(0.8, 0.02, 16, 50);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0x444444, transparent: true, opacity: 0.3 });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = Math.PI / 2;
    group.add(ring);

    return group;
}

export function animate() {
    requestAnimationFrame(animate);

    const time = Date.now() * 0.001;

    // Starfield rotation
    if (starMesh) {
        starMesh.rotation.y += 0.0005;
    }

    // Agent Core Pulse
    const core = scene.userData.core;
    if (core) {
        core.rotation.y -= 0.01;
        core.rotation.z += 0.005;
        const scale = 1 + Math.sin(time * 2) * 0.1;
        core.scale.set(scale, scale, scale);
    }

    // Strategy Nodes Animation
    strategyGroup.rotation.y += 0.002; // Rotate the whole ring

    Object.values(strategyNodes).forEach(node => {
        // Self rotation
        const mesh = node.children[0];
        if (mesh) {
            mesh.rotation.x += 0.02;
            mesh.rotation.y += 0.03;
        }

        // Smooth position update
        if (node.userData.targetPos) {
            node.position.x += (node.userData.targetPos.x - node.position.x) * 0.05;
            node.position.z += (node.userData.targetPos.z - node.position.z) * 0.05;
        }
    });

    controls.update();
    renderer.render(scene, camera);
}

function onWindowResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
}
