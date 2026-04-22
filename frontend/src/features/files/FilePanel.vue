<template>
  <div v-if="uploadedFiles.length || outputFiles.length" class="file-panel-section">
    <div class="file-panel-label" @click="open = !open">
      <span class="file-panel-toggle">{{ open ? '▾' : '▸' }}</span>
      파일 ({{ uploadedFiles.length + outputFiles.length }})
    </div>
    <div v-if="open" class="file-panel-list">
      <div v-if="uploadedFiles.length" class="file-panel-group">
        <div class="file-panel-group-label">Input</div>
        <div v-for="file in uploadedFiles" :key="'u-' + file.name" class="file-panel-item">
          <span class="file-name" :title="file.name">{{ file.name }}</span>
          <span class="file-size">{{ file.size }}</span>
          <button class="file-delete-btn" title="삭제" @click="$emit('delete-file', file.name)">&times;</button>
        </div>
      </div>
      <div v-if="outputFiles.length" class="file-panel-group">
        <div class="file-panel-group-label">Output</div>
        <div
          v-for="file in outputFiles"
          :key="'o-' + file.name"
          class="file-panel-item file-dl"
          @click="$emit('download', file.name)"
        >
          <span class="file-name" :title="file.name">{{ file.name }}</span>
          <span class="file-size">{{ file.size }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  outputFiles: {
    type: Array,
    required: true,
  },
  uploadedFiles: {
    type: Array,
    required: true,
  },
})

defineEmits(['delete-file', 'download'])

const open = ref(true)
</script>
