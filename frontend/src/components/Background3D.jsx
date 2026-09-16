import { useEffect, useRef } from "react";
import * as THREE from "three";

const NODE_COUNT = 110;
const LINK_DISTANCE = 5.5;
const BOUNDS = 14;

export default function Background3D() {
	const mountRef = useRef(null);

	useEffect(() => {
		const mount = mountRef.current;
		if (!mount) return undefined;

		const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
		const scene = new THREE.Scene();
		const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 100);
		camera.position.z = 16;

		const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
		renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
		renderer.setSize(window.innerWidth, window.innerHeight);
		mount.appendChild(renderer.domElement);

		const nodes = Array.from({ length: NODE_COUNT }, () => ({
			position: new THREE.Vector3(
				(Math.random() - 0.5) * BOUNDS * 2,
				(Math.random() - 0.5) * BOUNDS * 2,
				(Math.random() - 0.5) * BOUNDS,
			),
			velocity: new THREE.Vector3(
				(Math.random() - 0.5) * 0.012,
				(Math.random() - 0.5) * 0.012,
				(Math.random() - 0.5) * 0.008,
			),
		}));

		const pointGeometry = new THREE.BufferGeometry();
		const pointPositions = new Float32Array(NODE_COUNT * 3);
		pointGeometry.setAttribute("position", new THREE.BufferAttribute(pointPositions, 3));
		const pointMaterial = new THREE.PointsMaterial({ color: 0x9b8cff, size: 0.11, transparent: true, opacity: 0.85, depthWrite: false });
		scene.add(new THREE.Points(pointGeometry, pointMaterial));

		const lineGeometry = new THREE.BufferGeometry();
		const linePositions = new Float32Array(NODE_COUNT * NODE_COUNT * 2 * 3);
		lineGeometry.setAttribute("position", new THREE.BufferAttribute(linePositions, 3));
		const lineMaterial = new THREE.LineBasicMaterial({ color: 0x46d6b0, transparent: true, opacity: 0.18, depthWrite: false });
		const lines = new THREE.LineSegments(lineGeometry, lineMaterial);
		scene.add(lines);

		const mouse = { x: 0, y: 0 };
		const onMouseMove = (event) => {
			mouse.x = (event.clientX / window.innerWidth - 0.5) * 2;
			mouse.y = (event.clientY / window.innerHeight - 0.5) * 2;
		};
		window.addEventListener("mousemove", onMouseMove);

		function step() {
			nodes.forEach((node) => {
				node.position.add(node.velocity);
				if (Math.abs(node.position.x) > BOUNDS) node.velocity.x *= -1;
				if (Math.abs(node.position.y) > BOUNDS) node.velocity.y *= -1;
				if (Math.abs(node.position.z) > BOUNDS / 2) node.velocity.z *= -1;
			});

			const pointAttribute = pointGeometry.getAttribute("position");
			nodes.forEach((node, index) => pointAttribute.setXYZ(index, node.position.x, node.position.y, node.position.z));
			pointAttribute.needsUpdate = true;

			let vertexIndex = 0;
			const lineAttribute = lineGeometry.getAttribute("position");
			for (let first = 0; first < NODE_COUNT; first += 1) {
				for (let second = first + 1; second < NODE_COUNT; second += 1) {
					if (nodes[first].position.distanceTo(nodes[second].position) < LINK_DISTANCE) {
						lineAttribute.setXYZ(vertexIndex++, nodes[first].position.x, nodes[first].position.y, nodes[first].position.z);
						lineAttribute.setXYZ(vertexIndex++, nodes[second].position.x, nodes[second].position.y, nodes[second].position.z);
					}
				}
			}
			lineGeometry.setDrawRange(0, vertexIndex);
			lineAttribute.needsUpdate = true;
			camera.position.x += (mouse.x * 1.5 - camera.position.x) * 0.02;
			camera.position.y += (-mouse.y * 1.5 - camera.position.y) * 0.02;
			camera.lookAt(0, 0, 0);
		}

		let frameId;
		function animate() {
			if (!reducedMotion) step();
			renderer.render(scene, camera);
			frameId = requestAnimationFrame(animate);
		}
		animate();

		const onResize = () => {
			camera.aspect = window.innerWidth / window.innerHeight;
			camera.updateProjectionMatrix();
			renderer.setSize(window.innerWidth, window.innerHeight);
		};
		window.addEventListener("resize", onResize);

		return () => {
			cancelAnimationFrame(frameId);
			window.removeEventListener("resize", onResize);
			window.removeEventListener("mousemove", onMouseMove);
			pointGeometry.dispose();
			pointMaterial.dispose();
			lineGeometry.dispose();
			lineMaterial.dispose();
			renderer.dispose();
			if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
		};
	}, []);

	return <div ref={mountRef} className="background-3d" aria-hidden="true" />;
}
