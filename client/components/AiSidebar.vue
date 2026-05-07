<template>
  <!-- Mobile overlay -->
  <div
    v-if="isOpen"
    class="fixed inset-0 z-30 bg-black/40 md:hidden"
    @click="$emit('close')"
  ></div>

  <!-- Sidebar slides in from the right (matches FolderSidebar pattern but mirrored). -->
  <aside
    :class="[
      'fixed top-0 right-0 z-40 h-full w-96 flex flex-col',
      'bg-theme-background border-l border-theme-border',
      'transition-transform duration-300 ease-in-out',
      isOpen ? 'translate-x-0' : 'translate-x-full',
    ]"
  >
    <!-- Header: title + close button -->
    <div
      class="flex items-center justify-between px-4 py-3 border-b border-theme-border shrink-0"
    >
      <span
        class="text-xs font-bold uppercase text-theme-text-very-muted tracking-wider"
        >AI Chat</span
      >
      <div class="flex items-center gap-2">
        <button
          @click="refresh"
          class="text-theme-text-muted hover:text-theme-text transition-colors p-1 rounded"
          title="Reload chat from vault"
        >
          <svg viewBox="0 0 24 24" class="w-4 h-4 fill-current">
            <path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z" />
          </svg>
        </button>
        <button
          @click="$emit('close')"
          class="text-theme-text-muted hover:text-theme-text transition-colors p-1 rounded"
          title="Close"
        >
          <svg viewBox="0 0 24 24" class="w-4 h-4 fill-current">
            <path
              d="M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z"
            />
          </svg>
        </button>
      </div>
    </div>

    <!-- Tab strip: Vault always; Note/Folder/Tag visible only when current
         context provides a target. AI tools default-filter by the active tab's
         scope (see backend scope-context system prompt). -->
    <div
      class="flex border-b border-theme-border shrink-0 bg-theme-background-secondary"
    >
      <button
        :class="tabClass('vault')"
        @click="setActiveTab('vault')"
        :title="`Chat scoped to entire vault`"
      >
        🗂️ Vault
      </button>
      <button
        v-if="noteTabAvailable"
        :class="tabClass('note')"
        @click="setActiveTab('note')"
        :title="`Chat scoped to: ${currentNote}`"
      >
        📄 Note
      </button>
      <button
        v-if="folderTabAvailable"
        :class="tabClass('folder')"
        @click="setActiveTab('folder')"
        :title="`Chat scoped to folder: ${currentFolder}`"
      >
        📁 Folder
      </button>
      <button
        v-if="tagTabAvailable"
        :class="tabClass('tag')"
        @click="setActiveTab('tag')"
        :title="`Chat scoped to tag(s): ${currentTags.join('+')}`"
      >
        🏷️ Tag
      </button>
    </div>

    <!-- Scope target indicator below tabs (helps disambiguate which note/folder/tag) -->
    <div
      v-if="activeTab !== 'vault' && currentScopeTarget"
      class="px-3 py-1.5 text-xs text-theme-text-muted border-b border-theme-border bg-theme-background-secondary truncate shrink-0"
      :title="currentScopeTarget"
    >
      📎 {{ currentScopeTarget }}
    </div>

    <!-- Message list — scrollable. -->
    <div
      ref="messageScroll"
      class="flex-1 overflow-y-auto px-3 py-3 space-y-3 min-h-0"
    >
      <div
        v-if="messages.length === 0 && !isLoading"
        class="text-sm text-theme-text-very-muted italic text-center py-8"
      >
        Ask me about your vault. I can read notes, search, and answer questions.
      </div>

      <div
        v-for="(m, i) in messages"
        :key="i"
        :class="[
          'rounded-lg px-3 py-2 text-sm',
          m.role === 'user'
            ? 'bg-theme-accent/10 ml-6'
            : 'bg-theme-background-secondary mr-6',
        ]"
      >
        <div
          class="text-xs font-bold uppercase tracking-wider mb-1"
          :class="
            m.role === 'user'
              ? 'text-theme-accent'
              : 'text-theme-text-muted'
          "
        >
          {{ m.role === "user" ? "You" : "Claude" }}
        </div>
        <div class="whitespace-pre-wrap break-words">{{ m.content }}</div>
        <div
          v-if="m.elapsed_ms"
          class="text-xs text-theme-text-very-muted mt-1"
        >
          {{ (m.elapsed_ms / 1000).toFixed(1) }}s
        </div>
      </div>

      <div
        v-if="isLoading"
        class="flex items-center gap-2 text-sm text-theme-text-muted italic mr-6"
      >
        <svg
          class="animate-spin w-4 h-4 fill-current"
          viewBox="0 0 24 24"
        >
          <path
            d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z"
          />
        </svg>
        Claude is thinking…
      </div>

      <div
        v-if="error"
        class="rounded-lg px-3 py-2 text-sm bg-red-500/10 border border-red-500/30 text-red-400"
      >
        <div class="text-xs font-bold uppercase tracking-wider mb-1">
          Error
        </div>
        <div class="whitespace-pre-wrap break-words">{{ error }}</div>
      </div>
    </div>

    <!-- Input area. -->
    <div class="border-t border-theme-border p-3 shrink-0">
      <textarea
        ref="textarea"
        v-model="draft"
        @keydown.enter.exact.prevent="send"
        @keydown.enter.shift.exact="newline"
        :disabled="isLoading"
        rows="3"
        placeholder="Message Claude (Enter to send, Shift+Enter for newline)…"
        class="w-full px-3 py-2 text-sm rounded-lg border border-theme-border bg-theme-background-secondary text-theme-text placeholder-theme-text-very-muted resize-none focus:outline-none focus:ring-1 focus:ring-theme-accent disabled:opacity-50"
      ></textarea>
      <div class="flex justify-between items-center mt-2">
        <div class="text-xs text-theme-text-very-muted">
          {{ sessionId ? `Session: ${sessionId.slice(0, 8)}…` : "New session" }}
        </div>
        <button
          @click="send"
          :disabled="isLoading || !draft.trim()"
          class="px-3 py-1 text-sm rounded-md bg-theme-accent text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-theme-accent/90 transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, computed } from "vue";
import { aiChat, aiChatHistory } from "../api.js";

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  currentNote: { type: String, default: null },     // vault-relative .md path or null
  currentFolder: { type: String, default: null },   // vault-relative folder path or null
  currentTags: { type: Array, default: () => [] }, // active tag filters
});
defineEmits(["close"]);

const STORAGE_KEY_TAB = "fn_ai_active_tab";

const activeTab = ref(localStorage.getItem(STORAGE_KEY_TAB) || "vault");
const messages = ref([]);
const draft = ref("");
const sessionId = ref(null);
const isLoading = ref(false);
const error = ref(null);
const messageScroll = ref(null);

// ── Tab availability based on context ────────────────────────────────────────
const noteTabAvailable = computed(() => !!props.currentNote);
const folderTabAvailable = computed(() => !!props.currentFolder);
const tagTabAvailable = computed(
  () => Array.isArray(props.currentTags) && props.currentTags.length > 0
);

// ── Active scope target derived from currently-active tab ────────────────────
const currentScopeTarget = computed(() => {
  if (activeTab.value === "note") return props.currentNote;
  if (activeTab.value === "folder") return props.currentFolder;
  if (activeTab.value === "tag")
    return [...props.currentTags].sort().join("+");
  return null;
});

function tabClass(name) {
  const base = "flex-1 px-3 py-2 text-xs font-medium transition-colors";
  if (activeTab.value === name) {
    return `${base} text-theme-text border-b-2 border-theme-accent`;
  }
  return `${base} text-theme-text-muted hover:text-theme-text`;
}

function setActiveTab(name) {
  activeTab.value = name;
  localStorage.setItem(STORAGE_KEY_TAB, name);
  loadHistory();
}

function scrollToBottom() {
  nextTick(() => {
    if (messageScroll.value) {
      messageScroll.value.scrollTop = messageScroll.value.scrollHeight;
    }
  });
}

function newline(e) {
  // Default textarea behaviour — let Shift+Enter insert a newline
}

async function loadHistory() {
  if (!props.isOpen) return;
  try {
    const target = currentScopeTarget.value || "";
    const data = await aiChatHistory(activeTab.value, target);
    messages.value = data.messages || [];
    sessionId.value = data.session_id || null;
    error.value = null;
    scrollToBottom();
  } catch (e) {
    const detail =
      e?.response?.data?.detail || e?.message || "Failed to load chat history";
    error.value = detail;
  }
}

async function refresh() {
  await loadHistory();
}

async function send() {
  const text = draft.value.trim();
  if (!text || isLoading.value) return;
  messages.value.push({ role: "user", content: text });
  draft.value = "";
  scrollToBottom();
  isLoading.value = true;
  error.value = null;
  try {
    const data = await aiChat(text, activeTab.value, currentScopeTarget.value);
    sessionId.value = data.session_id;
    messages.value.push({
      role: "assistant",
      content: data.response,
      elapsed_ms: data.elapsed_ms,
    });
  } catch (e) {
    const detail =
      e?.response?.data?.detail ||
      e?.response?.data?.message ||
      e?.message ||
      "Unknown error";
    error.value = detail;
  } finally {
    isLoading.value = false;
    scrollToBottom();
  }
}

// If the active tab becomes unavailable (e.g., user navigates away from a
// note while on the Note tab), fall back to Vault tab gracefully.
function ensureValidTab() {
  if (activeTab.value === "note" && !noteTabAvailable.value) activeTab.value = "vault";
  if (activeTab.value === "folder" && !folderTabAvailable.value) activeTab.value = "vault";
  if (activeTab.value === "tag" && !tagTabAvailable.value) activeTab.value = "vault";
}

// Reload history when:
//   1. sidebar opens
//   2. active tab changes
//   3. scope target changes (e.g., navigated to a different note while on Note tab)
watch(() => props.isOpen, (open) => {
  if (open) {
    ensureValidTab();
    loadHistory();
  }
});
watch([noteTabAvailable, folderTabAvailable, tagTabAvailable], ensureValidTab);
watch(currentScopeTarget, () => {
  if (props.isOpen) loadHistory();
});

onMounted(() => {
  ensureValidTab();
  if (props.isOpen) loadHistory();
});

// Public method invoked by App.vue when the user clicks the
// "Identify Now" button in a meeting note's speaker-label banner.
// Switches to the requested scope tab and pre-fills the chat input
// with a structured prompt that gets the labeling flow rolling.
function startSpeakerLabelingFlow(scope = "note") {
  if (scope === "note" && noteTabAvailable.value) {
    setActiveTab("note");
  }
  draft.value =
    "Help me identify the unknown speakers in this meeting. " +
    "Use list_unknown_speakers_in_meeting to get the list, then for " +
    "each one tell me the longest quote + timestamp. I'll respond with " +
    "the person's name. After each name, call enroll_voiceprint to add " +
    "their sample to the registry, then call relabel_speakers_in_note " +
    "to update this note. Walk me through them one at a time.";
}

defineExpose({ startSpeakerLabelingFlow });
</script>
