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

    <!-- Tab strip — Stage 3 ships Vault tab only.
         Stages 7+ enable Note/Folder/Tag tabs conditionally. -->
    <div
      class="flex border-b border-theme-border shrink-0 bg-theme-background-secondary"
    >
      <button
        :class="[
          'flex-1 px-3 py-2 text-xs font-medium transition-colors',
          activeTab === 'vault'
            ? 'text-theme-text border-b-2 border-theme-accent'
            : 'text-theme-text-muted hover:text-theme-text',
        ]"
        @click="activeTab = 'vault'"
      >
        🗂️ Vault
      </button>
      <button
        disabled
        class="flex-1 px-3 py-2 text-xs font-medium text-theme-text-very-muted cursor-not-allowed opacity-50"
        title="Coming in Stage 7"
      >
        📄 Note
      </button>
      <button
        disabled
        class="flex-1 px-3 py-2 text-xs font-medium text-theme-text-very-muted cursor-not-allowed opacity-50"
        title="Coming in Stage 7"
      >
        📁 Folder
      </button>
      <button
        disabled
        class="flex-1 px-3 py-2 text-xs font-medium text-theme-text-very-muted cursor-not-allowed opacity-50"
        title="Coming in Stage 7"
      >
        🏷️ Tag
      </button>
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
import { ref, watch, nextTick, onMounted } from "vue";
import { aiChat, aiChatHistory } from "../api.js";

const props = defineProps({
  isOpen: { type: Boolean, default: false },
});
defineEmits(["close"]);

const activeTab = ref("vault");
const messages = ref([]);
const draft = ref("");
const sessionId = ref(null);
const isLoading = ref(false);
const error = ref(null);
const messageScroll = ref(null);

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
  // Vault tab is the only scope wired up in v1 Stage 6; Stage 7 adds others.
  try {
    const data = await aiChatHistory("vault");
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

// Reload-from-server button — useful when the chat note has been edited
// elsewhere (Obsidian, another browser session) or after a restart.
async function refresh() {
  await loadHistory();
}

async function send() {
  const text = draft.value.trim();
  if (!text || isLoading.value) return;
  // Optimistic: show user message immediately while the round-trip runs.
  // The server also persists this to the chat note before invoking the LLM.
  messages.value.push({ role: "user", content: text });
  draft.value = "";
  scrollToBottom();
  isLoading.value = true;
  error.value = null;
  try {
    const data = await aiChat(text, "vault");
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

// Load history once the sidebar is first shown, and again whenever it
// transitions from hidden → visible (in case another client added messages).
watch(
  () => props.isOpen,
  (open) => {
    if (open) loadHistory();
  }
);

onMounted(() => {
  if (props.isOpen) loadHistory();
});
</script>
