<script setup lang="ts">
import { computed } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
const props = defineProps<{ content: string }>();
const html = computed(() => {
  const parsed = marked.parse(props.content ?? "", {
    async: false,
    breaks: true,
  });
  // No remote images, embedded documents, forms or active HTML from model text.
  return DOMPurify.sanitize(parsed, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: [
      "img",
      "iframe",
      "form",
      "input",
      "button",
      "video",
      "audio",
      "style",
    ],
    FORBID_ATTR: ["style", "target"],
    ALLOW_DATA_ATTR: false,
  });
});
</script>
<template><div class="markdown-content" v-html="html" /></template>
