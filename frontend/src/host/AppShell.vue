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
        <section class="mode-switcher">
          <div class="mode-switcher-header">
            <h3>작업 모드</h3>
            <span class="mode-switcher-hint">스트리밍 중에는 변경할 수 없습니다.</span>
          </div>
          <div class="mode-switcher-buttons">
            <button
              v-for="option in modeOptions"
              :key="option.key"
              class="mode-switcher-btn"
              :class="{ active: mode === option.key }"
              :disabled="isStreaming"
              @click="setMode(option.key)"
            >
              <span class="mode-switcher-btn-label">{{ option.label }}</span>
              <span class="mode-switcher-btn-desc">{{ option.description }}</span>
            </button>
          </div>
        </section>

        <FilePanel
          v-if="showFilePanel"
          :open="panelOpen.files"
          :output-files="outputFiles"
          :uploaded-files="uploadedFiles"
          @download="downloadFile"
          @toggle="panelOpen.files = !panelOpen.files"
          @upload="doUploadAndNotify"
        />

        <section v-else class="mode-guide-card">
          <h3>{{ currentModeMeta.label }}</h3>
          <p>이 모드에서는 현재 구축된 온톨로지만 조회하며, 문서 업로드나 그래프 수정 도구는 사용하지 않습니다.</p>
          <p>스키마를 만들거나 엔티티를 저장하려면 온톨로지 구축 모드로 전환하세요.</p>
        </section>
      </div>

      <div class="sidebar-footer">
        <button class="btn-reset" @click="resetSession">새 대화</button>
      </div>
    </aside>

    <ChatPanel
      v-model:input-text="inputText"
      :examples="examples"
      :input-placeholder="currentModeMeta.inputPlaceholder"
      :is-neo4j-tool="isNeo4jTool"
      :is-streaming="isStreaming"
      :messages="messages"
      :mode-description="currentModeMeta.description"
      :mode-label="currentModeMeta.label"
      @download="downloadFile"
      @send="send"
      @send-example="send"
    />

    <aside class="right-panel">
      <div class="right-panel-header">
        <h3>세션 정보</h3>
      </div>
      <div class="right-panel-scroll">
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
      </div>
    </aside>
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
  currentModeMeta,
  doUploadAndNotify,
  downloadFile,
  entityCounts,
  examples,
  inputText,
  isNeo4jTool,
  isStreaming,
  messages,
  mode,
  modeOptions,
  neo4jConnected,
  outputFiles,
  panelOpen,
  refFiles,
  refreshAll,
  resetSession,
  schema,
  send,
  setMode,
  showFilePanel,
  skills,
  todos,
  uploadedFiles,
} = useOntologyStudio()
</script>
