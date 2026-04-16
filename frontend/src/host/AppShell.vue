<template>
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>Ontology Studio</h2>
        <div class="header-actions">
          <Neo4jStatusBadge :connected="neo4jConnected" />
          <button class="btn-icon" title="새로고침" @click="refreshAll">&#x21bb;</button>
        </div>
      </div>

      <div class="sidebar-scroll">
        <TodoPanel
          :open="panelOpen.todos"
          :todos="todos"
          @toggle="panelOpen.todos = !panelOpen.todos"
        />
        <OntologySchemaPanel
          :entity-counts="entityCounts"
          :open="panelOpen.schema"
          :schema="schema"
          @toggle="panelOpen.schema = !panelOpen.schema"
        />
        <ContextPanel
          :open="panelOpen.context"
          :ref-files="refFiles"
          :skills="skills"
          @toggle="panelOpen.context = !panelOpen.context"
        />
        <FilePanel
          :open="panelOpen.files"
          :output-files="outputFiles"
          :uploaded-files="uploadedFiles"
          @download="downloadFile"
          @toggle="panelOpen.files = !panelOpen.files"
          @upload="doUploadAndNotify"
        />
      </div>

      <div class="sidebar-footer">
        <button class="btn-reset" @click="resetSession">새 대화</button>
      </div>
    </aside>

    <ChatPanel
      v-model:input-text="inputText"
      :examples="examples"
      :is-neo4j-tool="isNeo4jTool"
      :is-streaming="isStreaming"
      :messages="messages"
      @download="downloadFile"
      @send="send"
      @send-example="send"
    />
  </div>
</template>

<script setup>
import ChatPanel from '../features/chat/ChatPanel.vue'
import FilePanel from '../features/files/FilePanel.vue'
import OntologySchemaPanel from '../features/ontology/OntologySchemaPanel.vue'
import ContextPanel from '../features/session/ContextPanel.vue'
import TodoPanel from '../features/session/TodoPanel.vue'
import { useOntologyStudio } from '../shared/hooks/useOntologyStudio.js'
import Neo4jStatusBadge from '../shared/ui/Neo4jStatusBadge.vue'

const {
  doUploadAndNotify,
  downloadFile,
  entityCounts,
  examples,
  inputText,
  isNeo4jTool,
  isStreaming,
  messages,
  neo4jConnected,
  outputFiles,
  panelOpen,
  refFiles,
  refreshAll,
  resetSession,
  schema,
  send,
  skills,
  todos,
  uploadedFiles,
} = useOntologyStudio()
</script>
