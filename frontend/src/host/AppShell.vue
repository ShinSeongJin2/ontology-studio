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

      <section v-if="sessions.length > 0" class="session-list">
        <h3>대화 기록</h3>
        <ul>
          <li
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: s.id === sessionId }"
            @click="switchSession(s.id)"
          >
            <span class="session-title">{{ s.title || '새 대화' }}</span>
            <span class="session-date">{{ formatSessionDate(s.updated_at) }}</span>
            <button
              class="session-delete"
              title="삭제"
              @click.stop="deleteSession(s.id)"
            >&times;</button>
          </li>
        </ul>
      </section>

      <div class="sidebar-footer">
        <button class="btn-reset" @click="handleNewSession">새 대화</button>
        <button class="btn-reset btn-danger" @click="confirmClearNeo4j">Neo4j 전체 초기화</button>
      </div>
    </aside>

    <main class="workspace-main">
      <OntologyBuildBriefPanel
        v-if="mode === 'build' && messages.length === 0"
        :can-start="isBuildBriefReady"
        :golden-questions="buildGoldenQuestions"
        :intent="buildIntent"
        :is-streaming="isStreaming"
        @add-question="addBuildGoldenQuestion"
        @import-questions="importBuildGoldenQuestions"
        @remove-question="removeBuildGoldenQuestion"
        @start-build="send"
        @update:intent="buildIntent = $event"
        @update:question="setBuildGoldenQuestion"
      />

      <ChatPanel
        v-else
        :can-send="canSend"
        :can-submit-build-feedback="canSubmitBuildFeedback"
        v-model:input-text="inputText"
        :examples="examples"
        :input-placeholder="effectiveInputPlaceholder"
        :is-neo4j-tool="isNeo4jTool"
        :is-streaming="isStreaming"
        :messages="messages"
        :mode-description="currentModeMeta.description"
        :mode-label="currentModeMeta.label"
        @download="downloadFile"
        @send="send"
        @send-example="send"
        @stop="stopStreaming"
        @submit-build-feedback="submitBuildFeedback"
      />
    </main>

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
import OntologyBuildBriefPanel from '../features/ontology/OntologyBuildBriefPanel.vue'
import OntologySchemaPanel from '../features/ontology/OntologySchemaPanel.vue'
import ContextPanel from '../features/session/ContextPanel.vue'
import TodoPanel from '../features/session/TodoPanel.vue'
import { useOntologyStudio } from '../shared/hooks/useOntologyStudio.js'
import Neo4jStatusBadge from '../shared/ui/Neo4jStatusBadge.vue'

const {
  addBuildGoldenQuestion,
  importBuildGoldenQuestions,
  buildGoldenQuestions,
  buildIntent,
  canSend,
  canSubmitBuildFeedback,
  clearNeo4jAll,
  createNewSession,
  currentModeMeta,
  deleteSession,
  doUploadAndNotify,
  downloadFile,
  effectiveInputPlaceholder,
  entityCounts,
  examples,
  inputText,
  isBuildBriefReady,
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
  removeBuildGoldenQuestion,
  resetSession,
  schema,
  send,
  sessionId,
  sessions,
  setBuildGoldenQuestion,
  setMode,
  showFilePanel,
  skills,
  stopStreaming,
  submitBuildFeedback,
  switchSession,
  todos,
  uploadedFiles,
} = useOntologyStudio()

function formatSessionDate(timestamp) {
  if (!timestamp) return ''
  const d = new Date(timestamp * 1000)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  if (isToday) {
    return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

async function handleNewSession() {
  await createNewSession()
}

async function confirmClearNeo4j() {
  if (!confirm('Neo4j의 모든 데이터(스키마, 엔티티, 관계, 문서, 청크)가 삭제됩니다.\n정말 초기화하시겠습니까?')) {
    return
  }
  await clearNeo4jAll()
  await resetSession()
}
</script>
