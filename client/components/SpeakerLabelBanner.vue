<template>
  <div
    v-if="hasUnknowns"
    class="mb-3 px-4 py-2 rounded-lg bg-amber-500/10 border border-amber-500/40 flex items-center gap-3"
  >
    <svg viewBox="0 0 24 24" class="w-5 h-5 fill-amber-500 shrink-0">
      <path
        d="M12,3A9,9 0 0,0 3,12H0L4,16L8,12H5A7,7 0 0,1 12,5A7,7 0 0,1 19,12A7,7 0 0,1 12,19C10.5,19 9.09,18.5 7.94,17.7L6.5,19.14C8.04,20.3 9.94,21 12,21A9,9 0 0,0 21,12A9,9 0 0,0 12,3M14,12A2,2 0 0,0 12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12Z"
      />
    </svg>
    <div class="flex-1 text-sm text-theme-text">
      <span class="font-medium">{{ unknownCount }} unknown speaker{{ unknownCount === 1 ? "" : "s" }}</span>
      detected — open the AI chat to identify them by voice + name.
    </div>
    <button
      @click="$emit('identify')"
      class="px-3 py-1 text-sm rounded bg-amber-500 text-white font-bold hover:bg-amber-600 transition-colors shrink-0"
    >
      Identify Now
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  noteContent: { type: String, default: "" },
});
defineEmits(["identify"]);

// Detect SPEAKER_NN labels still present in the note (frontmatter or body).
// Either format is the signal: meetings.py writes attendees with SPEAKER_NN
// for unmapped speakers, and the body/transcript section contains them too.
const unknownSpeakers = computed(() => {
  if (!props.noteContent) return [];
  const matches = props.noteContent.match(/\bSPEAKER_\d+\b/g) || [];
  return [...new Set(matches)].sort();
});
const unknownCount = computed(() => unknownSpeakers.value.length);
const hasUnknowns = computed(() => unknownCount.value > 0);
</script>
