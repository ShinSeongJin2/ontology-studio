<template>
  <CollapsiblePanel title="파일" :open="open" @toggle="$emit('toggle')">
    <div
      class="upload-zone"
      :class="{ 'drag-active': dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave="dragOver = false"
      @drop.prevent="handleDrop"
    >
      <input ref="fileInput" type="file" hidden multiple @change="handleFileSelect" />
      <div class="upload-label" @click="fileInput?.click()">
        <span class="upload-icon">+</span>
        <span>파일 업로드</span>
      </div>
    </div>

    <div v-if="uploadedFiles.length" class="file-list">
      <div class="ctx-label">Uploads</div>
      <div v-for="file in uploadedFiles" :key="file.name" class="file-item">
        <span class="fi-icon fi-upload">U</span>
        <span class="file-name">{{ file.name }}</span>
        <span class="file-size">{{ file.size }}</span>
      </div>
    </div>

    <div v-if="outputFiles.length" class="file-list">
      <div class="ctx-label">Output</div>
      <div
        v-for="file in outputFiles"
        :key="file.name"
        class="file-item file-dl"
        @click="$emit('download', file.name)"
      >
        <span class="fi-icon fi-output">D</span>
        <span class="file-name">{{ file.name }}</span>
        <span class="file-size">{{ file.size }}</span>
      </div>
    </div>
  </CollapsiblePanel>
</template>

<script setup>
import { ref } from 'vue'

import CollapsiblePanel from '../../shared/ui/CollapsiblePanel.vue'

defineProps({
  open: {
    type: Boolean,
    required: true,
  },
  outputFiles: {
    type: Array,
    required: true,
  },
  uploadedFiles: {
    type: Array,
    required: true,
  },
})

const emit = defineEmits(['download', 'toggle', 'upload'])

const dragOver = ref(false)
const fileInput = ref(null)

function emitUpload(files) {
  if (files?.length) {
    emit('upload', files)
  }
}

function handleFileSelect(event) {
  emitUpload(event.target.files)
  event.target.value = ''
}

function handleDrop(event) {
  dragOver.value = false
  emitUpload(event.dataTransfer.files)
}
</script>
