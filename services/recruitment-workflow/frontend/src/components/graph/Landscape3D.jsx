import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { percent, ringFor } from "./graphModel.js";
import styles from "./Landscape3D.module.css";

const JOB_RADIUS = 14;
const COLORS = { qualified: 0x2448c7, gate: 0xd9822b, below: 0xb8bec8, job: 0x15171c, floor: 0xeef0f5 };
const HOME = new THREE.Vector3(0, 30, 46);
// World units per score point above 0.40: a 0.90 match stands about 7 units tall,
// enough to read heights without hiding the jobs behind a forest of pins.
const HEIGHT_SCALE = 14;

/**
 * "Talent landscape": jobs stand on a circular floor, and each match is a pin next
 * to its job whose height is the final score. Positions are computed, not simulated,
 * so the scene is the same every time it opens. No edges are drawn: closeness to a
 * job already says which job a pin belongs to.
 *
 * Built directly on three.js (OrbitControls for the camera, CSS2DRenderer for the
 * labels so text stays crisp HTML). Lazy-loaded by GraphPage, so WebGL is only
 * initialized when a recruiter opens this view.
 *
 * Imperative handle: rotate(deg) and reset(), used by the page's camera buttons.
 */
const Landscape3D = forwardRef(function Landscape3D({ graph, names, jobIds, focusJobId, selectedCandidateId, onSelectCandidate }, ref) {
  const hostRef = useRef(null);
  const stateRef = useRef(null); // three.js objects that outlive renders
  const onSelectRef = useRef(onSelectCandidate);
  onSelectRef.current = onSelectCandidate;

  // Build the scene once per data set.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const width = host.clientWidth;
    const height = host.clientHeight;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    host.appendChild(renderer.domElement);

    const labels = new CSS2DRenderer();
    labels.setSize(width, height);
    labels.domElement.className = styles.labelLayer;
    host.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 400);
    camera.position.copy(HOME);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.45; // never under the floor
    controls.minDistance = 18;
    controls.maxDistance = 80;
    controls.target.set(0, 1.5, 0);
    controls.update();

    scene.add(new THREE.HemisphereLight(0xffffff, 0xdfe3ea, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.2);
    sun.position.set(12, 30, 18);
    scene.add(sun);

    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(JOB_RADIUS + 9, 96),
      new THREE.MeshBasicMaterial({ color: COLORS.floor }), // unlit: a flat, light floor
    );
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);

    const ringPoints = [];
    for (let i = 0; i <= 128; i += 1) {
      const a = (i / 128) * Math.PI * 2;
      ringPoints.push(new THREE.Vector3(Math.cos(a) * JOB_RADIUS, 0.02, Math.sin(a) * JOB_RADIUS));
    }
    const ring = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(ringPoints),
      new THREE.LineDashedMaterial({ color: 0xc9ced6, dashSize: 0.6, gapSize: 0.5 }),
    );
    ring.computeLineDistances();
    scene.add(ring);

    const titles = new Map(graph.jobs.map((job) => [job.job_id, job.title]));
    const pins = []; // { head, stem, edge, jobId, label }
    const jobs = []; // { jobId, marker, label }
    const stemGeo = new THREE.CylinderGeometry(0.06, 0.06, 1, 8);
    const headGeo = new THREE.SphereGeometry(1, 20, 14);

    jobIds.forEach((jobId, k) => {
      const a = (k / jobIds.length) * Math.PI * 2 - Math.PI / 2;
      const jx = Math.cos(a) * JOB_RADIUS;
      const jz = Math.sin(a) * JOB_RADIUS;
      const marker = new THREE.Mesh(
        new THREE.BoxGeometry(2.2, 0.5, 2.2),
        new THREE.MeshLambertMaterial({ color: COLORS.job, transparent: true }),
      );
      marker.position.set(jx, 0.25, jz);
      scene.add(marker);

      const tag = document.createElement("div");
      tag.className = styles.jobLabel;
      tag.textContent = titles.get(jobId) ?? "Job";
      const label = new CSS2DObject(tag);
      label.position.set(jx, -0.6, jz);
      scene.add(label);
      jobs.push({ jobId, marker, label, tag });

      const edges = graph.edges.filter((edge) => edge.job_id === jobId).sort((p, q) => q.final_score - p.final_score);
      edges.forEach((edge, i) => {
        const inner = i < 8;
        const slot = inner ? i : i - 8;
        const count = inner ? Math.min(8, edges.length) : Math.max(1, edges.length - 8);
        const b = a + (slot / count) * Math.PI * 2 + (inner ? 0.2 : 0.55);
        const r = inner ? 3.4 : 5.6;
        const x = jx + Math.cos(b) * r;
        const z = jz + Math.sin(b) * r;
        const h = Math.max(0.6, (edge.final_score - 0.4) * HEIGHT_SCALE);
        const kind = ringFor(edge);
        const material = new THREE.MeshLambertMaterial({ color: COLORS[kind], transparent: true });

        const stem = new THREE.Mesh(stemGeo, material);
        stem.scale.set(1, h, 1);
        stem.position.set(x, h / 2, z);
        scene.add(stem);

        const head = new THREE.Mesh(headGeo, material);
        const size = kind === "below" ? 0.32 : 0.45;
        head.scale.setScalar(size);
        head.position.set(x, h, z);
        head.userData = { candidateId: edge.candidate_id, baseSize: size };
        scene.add(head);

        const tagEl = document.createElement("div");
        tagEl.className = styles.pinLabel;
        tagEl.textContent = `${names.get(edge.candidate_id) ?? "Candidate"} · ${percent(edge.final_score)}%`;
        const pinLabel = new CSS2DObject(tagEl);
        pinLabel.position.set(x, h + 1, z);
        pinLabel.visible = false;
        scene.add(pinLabel);

        pins.push({ head, stem, material, edge, jobId, label: pinLabel });
      });
    });

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let downAt = null;
    function onPointerDown(event) {
      downAt = { x: event.clientX, y: event.clientY };
    }
    function onPointerUp(event) {
      // a drag is a camera move, not a click
      if (!downAt || Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(pins.map((p) => p.head))[0];
      if (hit) onSelectRef.current?.(hit.object.userData.candidateId);
    }
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    let frame = 0;
    const tween = { active: false };
    function animate() {
      frame = requestAnimationFrame(animate);
      if (tween.active) {
        camera.position.lerp(tween.to, 0.12);
        if (camera.position.distanceTo(tween.to) < 0.05) tween.active = false;
      }
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
    }
    animate();

    const resize = new ResizeObserver(() => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      labels.setSize(w, h);
    });
    resize.observe(host);

    stateRef.current = { camera, controls, pins, jobs, tween };

    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      scene.traverse((obj) => {
        obj.geometry?.dispose?.();
        if (obj.material) [].concat(obj.material).forEach((m) => m.dispose());
      });
      renderer.dispose();
      host.removeChild(renderer.domElement);
      host.removeChild(labels.domElement);
      stateRef.current = null;
    };
  }, [graph, names, jobIds]);

  // Highlight a job and the selected candidate without rebuilding the scene.
  useEffect(() => {
    const s = stateRef.current;
    if (!s) return;
    for (const job of s.jobs) {
      const dim = focusJobId && job.jobId !== focusJobId;
      job.marker.material.opacity = dim ? 0.25 : 1;
      job.tag.classList.toggle(styles.dim, Boolean(dim));
    }
    for (const pin of s.pins) {
      const dim = focusJobId && pin.jobId !== focusJobId;
      const selected = pin.edge.candidate_id === selectedCandidateId;
      pin.material.opacity = dim ? 0.12 : 1;
      pin.head.scale.setScalar(pin.head.userData.baseSize * (selected ? 1.6 : 1));
      pin.label.visible = selected && !dim;
    }
  }, [focusJobId, selectedCandidateId, graph, names, jobIds]);

  useImperativeHandle(ref, () => ({
    rotate(deg) {
      const s = stateRef.current;
      if (!s) return;
      const offset = s.camera.position.clone().sub(s.controls.target);
      offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), (deg * Math.PI) / 180);
      s.tween.to = s.controls.target.clone().add(offset);
      s.tween.active = true;
    },
    reset() {
      const s = stateRef.current;
      if (!s) return;
      s.controls.target.set(0, 1.5, 0);
      s.tween.to = HOME.clone();
      s.tween.active = true;
    },
  }));

  return <div ref={hostRef} className={styles.host} />;
});

export default Landscape3D;
