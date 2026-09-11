<script setup lang="ts">
import { toRef } from "vue";
import { createStreamingMarkdownVNodes, createVNodeRendererComponent } from "@fadedown/vue";
import "@fadedown/vue/styles.css";

// One assistant reply, rendered as streaming Markdown by fadedown: the frozen
// prefix is never re-rendered, half-written markup never flashes as raw text,
// and only the newly arrived words fade in.
const props = defineProps<{ text: string; streaming: boolean }>();

const { contentVNodes } = createStreamingMarkdownVNodes(toRef(props, "text"), toRef(props, "streaming"));
const Rendered = createVNodeRendererComponent(contentVNodes);
</script>

<template>
  <div class="chat-md"><Rendered /></div>
</template>
