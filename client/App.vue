<template>
  <LoadingIndicator
    ref="loadingIndicator"
    class="container mx-auto flex h-screen flex-col px-2 py-4 print:max-w-full"
  >
    <PrimeToast />
    <SearchModal v-model="isSearchModalVisible" />

    <!-- Tag Sidebar -->
    <TagSidebar
      :isOpen="isSidebarOpen"
      @close="isSidebarOpen = false"
      @tagsChanged="handleTagsChanged"
    />

    <!-- Folder Sidebar -->
    <FolderSidebar
      :isOpen="isFolderSidebarOpen"
      @close="isFolderSidebarOpen = false"
    />

    <!-- AI Chat Sidebar (slides from right) -->
    <AiSidebar
      :isOpen="isAiSidebarOpen"
      :currentNote="currentNote"
      :currentFolder="currentFolder"
      :currentTags="activeTags"
      @close="isAiSidebarOpen = false"
    />

    <!-- Drop audio anywhere on the window to transcribe (Stage 9) -->
    <AudioDropZone v-if="route.name !== 'login'" />

    <!-- Main content area with fixed header and scrollable content -->
    <div
      :class="[
        'flex flex-col flex-1 min-h-0 transition-all duration-300',
        (isSidebarOpen || isFolderSidebarOpen) ? 'md:ml-72' : 'md:ml-0',
        isAiSidebarOpen ? 'md:mr-96' : 'md:mr-0',
      ]"
    >
      <!-- Fixed NavBar - does not scroll -->
      <div class="shrink-0">
        <NavBar
          v-if="showNavBar"
          ref="navBar"
          :class="{ 'print:hidden': route.name == 'note' }"
          :hide-logo="true"
          :isSidebarOpen="isSidebarOpen"
          :isFolderSidebarOpen="isFolderSidebarOpen"
          :isAiSidebarOpen="isAiSidebarOpen"
          @toggleSearchModal="toggleSearchModal"
          @toggleSidebar="openTagSidebar"
          @toggleFolderSidebar="openFolderSidebar"
          @toggleAiSidebar="toggleAiSidebar"
        />
      </div>

      <!-- Scrollable content area -->
      <div class="flex-1 overflow-y-auto min-h-0">
        <RouterView :activeTags="activeTags" />
      </div>
    </div>
  </LoadingIndicator>
</template>

<script setup>
import Mousetrap from "mousetrap";
import "mousetrap/plugins/global-bind/mousetrap-global-bind";
import { useToast } from "primevue/usetoast";
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
// Below: derive AiSidebar's scope context from the current route.
import { RouterView, useRoute } from "vue-router";

import { apiErrorHandler, getConfig, getPrefs } from "./api.js";
import PrimeToast from "./components/PrimeToast.vue";
import TagSidebar from "./components/TagSidebar.vue";
import FolderSidebar from "./components/FolderSidebar.vue";
import AiSidebar from "./components/AiSidebar.vue";
import AudioDropZone from "./components/AudioDropZone.vue";
import { useGlobalStore } from "./globalStore.js";
import { loadTheme, initThemeListener, cleanupThemeListener } from "./helpers.js";
import NavBar from "./partials/NavBar.vue";
import SearchModal from "./partials/SearchModal.vue";
import LoadingIndicator from "./components/LoadingIndicator.vue";
import router from "./router.js";
import { initSettingsStore } from "./calloutStore.js";
import { loadTagColors } from "./tagColorStore.js";

const globalStore = useGlobalStore();
const isSearchModalVisible = ref(false);
// Persist sidebar open/closed state across page reloads
// ── Sidebar state — both persisted, mutually exclusive ───────────────────────
const isSidebarOpen = ref(localStorage.getItem("fn_sidebar_open") === "true");
const isFolderSidebarOpen = ref(localStorage.getItem("fn_folder_sidebar_open") === "true");
const isAiSidebarOpen = ref(localStorage.getItem("fn_ai_sidebar_open") === "true");
const activeTags = ref([]);

// Persist sidebar states whenever they change
watch(isSidebarOpen, (val) => {
  localStorage.setItem("fn_sidebar_open", String(val));
});
watch(isFolderSidebarOpen, (val) => {
  localStorage.setItem("fn_folder_sidebar_open", String(val));
});
watch(isAiSidebarOpen, (val) => {
  localStorage.setItem("fn_ai_sidebar_open", String(val));
});
const loadingIndicator = ref();
const navBar = ref();
const route = useRoute();
const toast = useToast();

// '/' to search
Mousetrap.bind("/", () => {
  if (route.name !== "login") {
    toggleSearchModal();
    return false;
  }
});

// 'CTRL + ALT/OPT + N' to create new note
Mousetrap.bindGlobal("ctrl+alt+n", () => {
  if (route.name !== "login") {
    router.push({ name: "new" });
    return false;
  }
});

// 'CTRL + ALT/OPT + H' to go to home
Mousetrap.bindGlobal("ctrl+alt+h", () => {
  if (route.name !== "login") {
    if (globalStore.homeNoteEnabled && globalStore.homeNote) {
      router.push({ name: "note", params: { title: globalStore.homeNote } });
    } else {
      router.push({ name: "home" });
    }
    return false;
  }
});

// 'CTRL + ALT/OPT + T' to toggle tag sidebar (mutually exclusive)
Mousetrap.bindGlobal("ctrl+alt+t", () => {
  if (route.name !== "login") {
    openTagSidebar();
    return false;
  }
});

getConfig()
  .then(async (data) => {
    globalStore.config = data;
    await initSettingsStore();
    // Load show_button_labels preference so it persists across page refreshes.
    // We do this after auth is confirmed (getConfig succeeds only when authenticated).
    try {
      const prefs = await getPrefs();
      globalStore.showButtonLabels = prefs.show_button_labels !== false;
      globalStore.homeNoteEnabled  = prefs.home_note_enabled === true;
      globalStore.homeNote         = prefs.home_note || '';

      // If a custom home note is configured and we're currently on the
      // default home route, navigate there immediately on startup.
      if (
        globalStore.homeNoteEnabled &&
        globalStore.homeNote &&
        router.currentRoute.value.name === 'home'
      ) {
        router.replace({ name: 'note', params: { title: globalStore.homeNote } });
      }
    } catch {
      // Non-fatal: defaults apply
    }
    loadingIndicator.value.setLoaded();
  })
  .catch((error) => {
    apiErrorHandler(error, toast);
    loadingIndicator.value.setFailed();
  });

const showNavBar = computed(() => {
  return route.name !== "login";
});

// ── Scope-context for AI sidebar tabs ────────────────────────────────────────
// Note tab is enabled when viewing a /note/<title> route.
// Folder tab is enabled when on /search with a folder= query param (which is
// how FolderSidebar navigates to a folder view).
// Tag tab is enabled whenever activeTags has any entries.
// Vault tab is always enabled.
const currentNote = computed(() => {
  if (route.name === "note" && route.params.title) {
    // route.params.title may already include the .md suffix or not depending
    // on how the link was constructed; ensure .md ext for canonical chat path
    const t = route.params.title;
    return t.endsWith(".md") ? t : `${t}.md`;
  }
  return null;
});
const currentFolder = computed(() => {
  if (route.name === "search" && route.query.folder) {
    return route.query.folder;
  }
  return null;
});

function toggleSearchModal() {
  isSearchModalVisible.value = !isSearchModalVisible.value;
}

// ── Sidebar mutual-exclusion helpers ─────────────────────────────────────────
// Opening one sidebar always closes the other so they never overlap.
// Clicking the button of an already-open sidebar closes it (toggle behaviour).
function openTagSidebar() {
  if (isSidebarOpen.value) {
    // Already open — close it
    isSidebarOpen.value = false;
  } else {
    isFolderSidebarOpen.value = false;  // close folder sidebar first
    isSidebarOpen.value = true;
  }
}

function openFolderSidebar() {
  if (isFolderSidebarOpen.value) {
    // Already open — close it
    isFolderSidebarOpen.value = false;
  } else {
    isSidebarOpen.value = false;  // close tag sidebar first
    isFolderSidebarOpen.value = true;
  }
}

// AI Sidebar lives on the right and does NOT mutually exclude the
// left-side Tag/Folder sidebars — you can have both open at once.
function toggleAiSidebar() {
  isAiSidebarOpen.value = !isAiSidebarOpen.value;
}

function handleTagsChanged(tags) {
  activeTags.value = tags;
  if (tags.length > 0) {
    // For each selected tag, use a Whoosh prefix wildcard query:
    // "tags:bookmark*" matches both "bookmark" (exact) and "bookmark/todo" (nested).
    // This means clicking "bookmark" shows notes with #bookmark AND #bookmark/todo etc.
    // Multiple tags use OR logic.
    const tagQuery = tags.map((t) => `tags:${t}*`).join(" OR ");
    router.push({ name: "search", query: { term: tagQuery } });
  }
}

// Initialize theme on mount
onMounted(() => {
  loadTheme();
loadTagColors(); // pre-load tag color config so chips render correctly on first paint
  initThemeListener();
});

// Cleanup on unmount
onUnmounted(() => {
  cleanupThemeListener();
});
</script>