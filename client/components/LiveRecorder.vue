<template>
  <!-- Bottom-pinned status bar — visible during recording AND finalization. -->
  <div
    v-if="state !== 'idle' && state !== 'error'"
    :class="[
      'fixed bottom-0 left-0 right-0 z-50 text-white shadow-2xl',
      state === 'finalizing' ? 'bg-blue-600' : 'bg-red-600',
    ]"
  >
    <!-- Recording state -->
    <div v-if="state !== 'finalizing'" class="px-4 py-2 flex items-center gap-3">
      <span class="relative flex h-3 w-3 shrink-0">
        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
        <span class="relative inline-flex rounded-full h-3 w-3 bg-red-300"></span>
      </span>
      <span class="font-mono text-sm shrink-0">{{ formattedElapsed }}</span>
      <span class="text-xs opacity-90 shrink-0">
        · {{ windowsCompleted }} window{{ windowsCompleted === 1 ? "" : "s" }}
        transcribed
      </span>
      <span
        class="text-sm flex-1 truncate italic opacity-80"
        :title="latestTranscript"
      >
        {{ latestTranscript || "Listening…" }}
      </span>
      <button
        @click="stop"
        class="px-3 py-1 text-sm rounded bg-white text-red-600 font-bold hover:bg-red-50 transition-colors shrink-0"
      >
        ⏹ Stop
      </button>
    </div>

    <!-- Finalizing state — diarize + LLM (~5-10 min for typical meetings) -->
    <div v-else class="px-4 py-2 flex items-center gap-3">
      <svg viewBox="0 0 24 24" class="w-5 h-5 fill-current animate-spin shrink-0">
        <path d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z" />
      </svg>
      <span class="text-sm flex-1 truncate">
        {{ finalizeMessage || "Finalizing…" }}
      </span>
      <span class="text-xs opacity-75 shrink-0">phase: {{ finalizePhase || "starting" }}</span>
    </div>
  </div>

  <!-- Error toast -->
  <div
    v-if="state === 'error'"
    class="fixed bottom-4 right-4 z-50 max-w-md bg-red-500/10 border border-red-500/40 rounded-lg shadow-2xl p-4"
  >
    <div class="font-semibold text-red-400">Recording failed</div>
    <div class="text-xs text-theme-text-muted mt-1 break-words">
      {{ errorMessage }}
    </div>
    <button
      @click="state = 'idle'"
      class="mt-2 text-xs px-2 py-1 rounded text-theme-text-muted hover:text-theme-text"
    >
      Dismiss
    </button>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { getStoredToken } from "../tokenStorage.js";

const props = defineProps({
  category: { type: String, default: "work" },
});
const emit = defineEmits(["state-changed", "note-created"]);
const router = useRouter();

// state machine: idle → connecting → recording → stopping → finalizing → idle
const state = ref("idle");
const errorMessage = ref("");
const elapsedMs = ref(0);
const windowsCompleted = ref(0);
const latestTranscript = ref("");
const notePath = ref(null);
const finalizePhase = ref("");
const finalizeMessage = ref("");

let ws = null;
let mediaStream = null;
let mediaRecorder = null;
let elapsedTimer = null;
let startMonotonic = 0;
let nextWindowIndex = 0;
let recorderShouldRestart = false;

const WINDOW_SIZE_MS = 30000; // 30 seconds per window — matches DESIGN.md

const formattedElapsed = computed(() => {
  const total = Math.round(elapsedMs.value / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
});

defineExpose({ start, stop, state });

async function start() {
  if (state.value !== "idle" && state.value !== "error") return;
  errorMessage.value = "";
  windowsCompleted.value = 0;
  latestTranscript.value = "";
  nextWindowIndex = 0;
  notePath.value = null;
  state.value = "connecting";
  emit("state-changed", state.value);

  // Check mic permission
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    state.value = "error";
    errorMessage.value = `Microphone access denied: ${e.message}`;
    emit("state-changed", state.value);
    return;
  }

  // Open WebSocket
  const token = getStoredToken();
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${proto}//${window.location.host}/ws/record?token=${encodeURIComponent(
    token || ""
  )}&category=${encodeURIComponent(props.category)}`;
  try {
    ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
      setTimeout(() => reject(new Error("WebSocket connect timeout")), 5000);
    });
  } catch (e) {
    state.value = "error";
    errorMessage.value = `Cannot connect to recorder: ${e?.message || e}`;
    cleanup();
    emit("state-changed", state.value);
    return;
  }

  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.type === "started") {
      notePath.value = msg.note_path;
      emit("note-created", msg.note_path);
      navigateToNote(msg.note_path);
    } else if (msg.type === "transcript") {
      windowsCompleted.value++;
      latestTranscript.value = msg.text;
    } else if (msg.type === "error") {
      // Per-window transcribe errors are non-fatal; finalization errors are.
      if (msg.phase === "finalize" || msg.phase === "import") {
        state.value = "error";
        errorMessage.value = msg.message;
        emit("state-changed", state.value);
      } else {
        console.error("recorder error:", msg.message);
        latestTranscript.value = `(transcribe error: ${msg.message})`;
      }
    } else if (msg.type === "stopped") {
      // Server received stop — finalization will follow with progress messages
      state.value = "finalizing";
      finalizePhase.value = "starting";
      finalizeMessage.value = "Finalizing recording…";
      emit("state-changed", state.value);
    } else if (msg.type === "progress") {
      finalizePhase.value = msg.phase || "";
      finalizeMessage.value = msg.message || "";
    } else if (msg.type === "finalized") {
      // Pipeline complete — navigate to the new note (different path than the
      // placeholder, since meetings.py derives a title from the LLM output)
      notePath.value = msg.note_path;
      state.value = "idle";
      emit("state-changed", state.value);
      navigateToNote(msg.note_path);
      // Show a one-shot summary toast through finalizeMessage so the user
      // knows what happened
      const ident = msg.speakers_identified || 0;
      const total = msg.speakers_total || 0;
      const unknown = msg.speakers_unknown || 0;
      finalizeMessage.value =
        `Meeting note ready · ${ident}/${total} speakers identified` +
        (unknown ? ` · ${unknown} unknown — open the note to label` : "");
    }
  };
  ws.onclose = () => {
    if (state.value === "recording" || state.value === "stopping") {
      state.value = "error";
      errorMessage.value = "Recorder connection closed unexpectedly";
      emit("state-changed", state.value);
    }
    cleanup();
  };

  // Send start, then begin recording windows
  ws.send(JSON.stringify({ type: "start" }));
  state.value = "recording";
  emit("state-changed", state.value);
  startMonotonic = performance.now();
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    elapsedMs.value = performance.now() - startMonotonic;
  }, 250);

  recorderShouldRestart = true;
  startNextWindow();
}

function startNextWindow() {
  if (!mediaStream || state.value !== "recording") return;
  const opts = { mimeType: "audio/webm;codecs=opus" };
  let recorder;
  try {
    recorder = new MediaRecorder(mediaStream, opts);
  } catch (e) {
    // Fallback: let browser pick a supported mime
    recorder = new MediaRecorder(mediaStream);
  }
  mediaRecorder = recorder;
  const chunks = [];
  const myWindowIndex = nextWindowIndex++;
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data);
  };
  recorder.onstop = async () => {
    if (chunks.length === 0) return;
    const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "window", index: myWindowIndex }));
      ws.send(await blob.arrayBuffer());
    }
    if (recorderShouldRestart) startNextWindow();
  };
  recorder.start();
  // Stop after WINDOW_SIZE_MS to flush the window
  setTimeout(() => {
    if (recorder.state !== "inactive") recorder.stop();
  }, WINDOW_SIZE_MS);
}

function navigateToNote(absPath) {
  // absPath is a vault-absolute filesystem path; convert to flatnotes route
  const VAULT_ROOT = "/Users/ben/Notes/Notes/";
  let p = absPath;
  if (p.startsWith(VAULT_ROOT)) p = p.slice(VAULT_ROOT.length);
  if (p.endsWith(".md")) p = p.slice(0, -3);
  router.push({ name: "note", params: { title: p } });
}

async function stop() {
  if (state.value !== "recording") return;
  state.value = "stopping";
  emit("state-changed", state.value);
  recorderShouldRestart = false;
  // Flush final window
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  // Wait briefly for the final window to upload, then send stop
  await new Promise((r) => setTimeout(r, 1000));
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop" }));
  }
}

function cleanup() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
  mediaRecorder = null;
  ws = null;
}

onUnmounted(cleanup);
</script>
