<template>
  <!-- Full-window overlay — only visible while a file is being dragged onto the window. -->
  <div
    v-if="isDragging"
    class="fixed inset-0 z-50 flex items-center justify-center pointer-events-none bg-theme-accent/10 backdrop-blur-sm"
  >
    <div
      class="border-4 border-dashed border-theme-accent rounded-2xl px-12 py-10 text-center bg-theme-background/95 shadow-2xl"
    >
      <svg
        viewBox="0 0 24 24"
        class="w-16 h-16 fill-theme-accent mx-auto mb-3"
      >
        <path
          d="M12,3L20,7.58V16.42L12,21L4,16.42V7.58L12,3M11,8.5V13.5L8.71,11.21L7.29,12.62L12,17.33L16.71,12.62L15.29,11.21L13,13.5V8.5H11Z"
        />
      </svg>
      <div class="text-xl font-bold text-theme-text">
        Drop audio file to transcribe
      </div>
      <div class="text-sm text-theme-text-muted mt-1">
        .wav .ogg .mp3 .m4a .opus .flac .aac .webm
      </div>
    </div>
  </div>

  <!-- Status bar shown while transcription is running (spans top of page). -->
  <div
    v-if="status === 'uploading' || status === 'transcribing'"
    class="fixed top-0 left-0 right-0 z-50 bg-theme-accent text-white text-sm px-4 py-2 flex items-center gap-3"
  >
    <svg viewBox="0 0 24 24" class="w-5 h-5 fill-current animate-spin">
      <path d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z" />
    </svg>
    <span class="flex-1 truncate">
      <span v-if="status === 'uploading'">Uploading {{ activeFileName }}…</span>
      <span v-else>
        Transcribing {{ activeFileName }} ({{ elapsedSeconds }}s elapsed —
        ~5-10 min for a typical meeting)
      </span>
    </span>
    <button
      v-if="status === 'transcribing'"
      @click="abort"
      class="text-xs underline opacity-80 hover:opacity-100"
      title="Note: cannot actually cancel the server-side pipeline; this only hides the indicator. The transcription will still complete and appear in your vault."
    >
      Hide
    </button>
  </div>

  <!-- Toast on completion -->
  <div
    v-if="status === 'done' && lastResult"
    class="fixed bottom-4 right-4 z-50 max-w-md bg-theme-background border border-theme-border rounded-lg shadow-2xl p-4"
  >
    <div class="flex items-start gap-3">
      <svg viewBox="0 0 24 24" class="w-5 h-5 fill-green-500 shrink-0 mt-0.5">
        <path
          d="M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22A10,10 0 0,1 2,12A10,10 0 0,1 12,2M11,16.5L18,9.5L16.59,8.09L11,13.67L7.91,10.59L6.5,12L11,16.5Z"
        />
      </svg>
      <div class="flex-1 min-w-0">
        <div class="font-semibold text-theme-text">
          Meeting note ready
        </div>
        <div class="text-xs text-theme-text-muted mt-1">
          {{ lastResult.speakers_identified }} of
          {{ lastResult.speakers_total }} speakers identified
          <span v-if="lastResult.speakers_unknown">
            · {{ lastResult.speakers_unknown }} unknown
          </span>
        </div>
        <div class="mt-2 flex gap-2">
          <button
            @click="openMeeting"
            class="text-xs px-2 py-1 rounded bg-theme-accent text-white hover:bg-theme-accent/90"
          >
            Open
          </button>
          <button
            @click="dismiss"
            class="text-xs px-2 py-1 rounded text-theme-text-muted hover:text-theme-text"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- Error toast -->
  <div
    v-if="status === 'error'"
    class="fixed bottom-4 right-4 z-50 max-w-md bg-red-500/10 border border-red-500/40 rounded-lg shadow-2xl p-4"
  >
    <div class="font-semibold text-red-400">Transcription failed</div>
    <div class="text-xs text-theme-text-muted mt-1 break-words">
      {{ errorMessage }}
    </div>
    <button
      @click="dismiss"
      class="mt-2 text-xs px-2 py-1 rounded text-theme-text-muted hover:text-theme-text"
    >
      Dismiss
    </button>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { getStoredToken } from "../tokenStorage.js";

const router = useRouter();

const isDragging = ref(false);
const status = ref("idle"); // idle | uploading | transcribing | done | error
const activeFileName = ref("");
const elapsedSeconds = ref(0);
const lastResult = ref(null);
const errorMessage = ref("");

let dragDepth = 0; // count of nested dragenter/dragleave events
let elapsedTimer = null;

const ALLOWED_EXT = [
  ".wav", ".ogg", ".mp3", ".m4a", ".opus", ".flac", ".aac", ".webm",
];

function isAudioFile(file) {
  if (file.type && file.type.startsWith("audio/")) return true;
  const lower = (file.name || "").toLowerCase();
  return ALLOWED_EXT.some((e) => lower.endsWith(e));
}

function onDragEnter(e) {
  if (e.dataTransfer && Array.from(e.dataTransfer.items || []).length > 0) {
    dragDepth++;
    isDragging.value = true;
    e.preventDefault();
  }
}
function onDragLeave(e) {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) isDragging.value = false;
}
function onDragOver(e) {
  if (isDragging.value) e.preventDefault(); // required to allow drop
}
async function onDrop(e) {
  e.preventDefault();
  dragDepth = 0;
  isDragging.value = false;
  const files = Array.from(e.dataTransfer.files || []);
  if (files.length === 0) return;
  const file = files.find(isAudioFile);
  if (!file) {
    status.value = "error";
    errorMessage.value = `No audio file found in drop. Allowed: ${ALLOWED_EXT.join(", ")}`;
    return;
  }
  await uploadAndTranscribe(file);
}

async function uploadAndTranscribe(file) {
  activeFileName.value = file.name;
  status.value = "uploading";
  errorMessage.value = "";
  lastResult.value = null;
  const fd = new FormData();
  fd.append("audio_file", file);
  fd.append("category", "work"); // Stage 9: defaults to work; Stage 11+ adds modal for picking
  const startTime = Date.now();
  elapsedSeconds.value = 0;
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    elapsedSeconds.value = Math.round((Date.now() - startTime) / 1000);
  }, 1000);
  try {
    const res = await fetch("/api/meetings/upload", {
      method: "POST",
      body: fd,
      headers: getAuthHeader(),
    });
    if (res.ok) {
      // Switch indicator from "uploading" to "transcribing" after upload completes
      status.value = "transcribing";
    }
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    lastResult.value = data;
    status.value = "done";
  } catch (err) {
    status.value = "error";
    errorMessage.value = err?.message || String(err);
  } finally {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }
}

function getAuthHeader() {
  // Match the same Bearer-token pattern the axios api uses; for fetch() we
  // attach it manually since we're outside the axios interceptor.
  const tok = getStoredToken();
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

function openMeeting() {
  if (!lastResult.value?.meeting_note_path) return;
  // meeting_note_path is an absolute filesystem path; convert to flatnotes
  // route by stripping the vault root + ".md" suffix.
  const VAULT_ROOT = "/Users/ben/Notes/Notes/";
  let p = lastResult.value.meeting_note_path;
  if (p.startsWith(VAULT_ROOT)) p = p.slice(VAULT_ROOT.length);
  if (p.endsWith(".md")) p = p.slice(0, -3);
  router.push({ name: "note", params: { title: p } });
  dismiss();
}

function dismiss() {
  status.value = "idle";
  lastResult.value = null;
  errorMessage.value = "";
}

function abort() {
  // Doesn't actually cancel the backend; just hides the indicator.
  status.value = "idle";
}

onMounted(() => {
  window.addEventListener("dragenter", onDragEnter);
  window.addEventListener("dragleave", onDragLeave);
  window.addEventListener("dragover", onDragOver);
  window.addEventListener("drop", onDrop);
});
onUnmounted(() => {
  window.removeEventListener("dragenter", onDragEnter);
  window.removeEventListener("dragleave", onDragLeave);
  window.removeEventListener("dragover", onDragOver);
  window.removeEventListener("drop", onDrop);
  if (elapsedTimer) clearInterval(elapsedTimer);
});
</script>
