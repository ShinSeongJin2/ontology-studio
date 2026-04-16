<template>
  <main class="chat-main">
    <div class="chat-messages">
      <div v-if="messages.length === 0" class="welcome">
        <h1>Ontology Studio</h1>
        <div class="welcome-mode">{{ modeLabel }}</div>
        <p>{{ modeDescription }}</p>
        <div class="examples">
          <button
            v-for="example in examples"
            :key="example"
            class="example-btn"
            @click="$emit('send-example', example)"
          >
            {{ example }}
          </button>
        </div>
      </div>

      <template v-for="(msg, index) in messages" :key="index">
        <div v-if="msg.role === 'user'" class="msg msg-user">
          <div class="msg-bubble">
            <div class="msg-text">{{ msg.text }}</div>
            <div v-if="msg.buildBrief" class="msg-brief">
              <div class="msg-brief-intent">
                <strong>의도</strong>
                <span>{{ msg.buildBrief.intent }}</span>
              </div>
              <div class="msg-brief-questions">
                <strong>Golden Question</strong>
                <ul>
                  <li v-for="question in msg.buildBrief.goldenQuestions" :key="question">
                    {{ question }}
                  </li>
                </ul>
              </div>
            </div>
            <div v-if="msg.buildFeedback?.length" class="msg-brief">
              <div class="msg-brief-questions">
                <strong>개선 피드백</strong>
                <ul>
                  <li v-for="item in msg.buildFeedback" :key="item.question">
                    {{ item.question }}: {{ item.feedback || (item.verdict === 'correct' ? '정답 처리' : '수정 필요') }}
                  </li>
                </ul>
              </div>
            </div>
            <div v-if="msg.files && msg.files.length" class="msg-files">
              <span v-for="file in msg.files" :key="file" class="msg-file-chip">{{ file }}</span>
            </div>
          </div>
        </div>

        <div v-else-if="msg.role === 'assistant'" class="msg msg-assistant">
          <div class="msg-avatar">AI</div>
          <div class="msg-body">
            <details
              v-if="getToolGroups(msg).length"
              class="tool-activity"
              :open="hasRunningTools(msg)"
            >
              <summary class="tool-activity-summary">
                <span
                  class="tool-activity-dot"
                  :class="hasRunningTools(msg) ? 'running' : 'done'"
                ></span>
                <span class="tool-activity-label">{{ getToolSummaryLabel(msg) }}</span>
              </summary>
              <div class="tool-activity-list">
                <div
                  v-for="(tool, ti) in getToolGroups(msg)"
                  :key="ti"
                  class="tool-activity-item"
                >
                  <div
                    class="tool-activity-header"
                    :class="{ 'tool-neo4j': isNeo4jTool(tool.name) }"
                  >
                    <span
                      class="tool-badge"
                      :class="{ 'badge-neo4j': isNeo4jTool(tool.name) }"
                    >
                      {{ isNeo4jTool(tool.name) ? 'NEO4J' : 'TOOL' }}
                    </span>
                    <code>{{ tool.name }}</code>
                    <span v-if="!tool.done" class="tool-running">실행 중...</span>
                    <span v-else class="tool-done-icon">&#10003;</span>
                  </div>
                  <details v-if="tool.result" class="tool-result-detail">
                    <summary>결과 보기</summary>
                    <pre>{{ tool.result }}</pre>
                  </details>
                </div>
              </div>
            </details>

            <MarkdownContent
              v-if="getTextContent(msg)"
              :content="getTextContent(msg)"
            />

            <div v-if="msg.files && msg.files.length" class="msg-output-files">
              <button
                v-for="file in msg.files"
                :key="file"
                class="btn-file-dl"
                @click="$emit('download', file)"
              >
                {{ file }}
              </button>
            </div>

            <GoldenQuestionReviewPanel
              v-if="msg.buildReport"
              :can-submit="canSubmitBuildFeedback(msg)"
              :is-streaming="isStreaming"
              :report="msg.buildReport"
              @set-verdict="(reviewIndex, verdict) => setBuildVerdict(msg, reviewIndex, verdict)"
              @submit-feedback="$emit('submit-build-feedback', msg)"
              @update-feedback="(reviewIndex, value) => updateBuildFeedback(msg, reviewIndex, value)"
            />
          </div>
        </div>
      </template>

      <div v-if="isStreaming" class="msg msg-assistant">
        <div class="msg-avatar">AI</div>
        <div class="msg-body"><span class="typing"><span /><span /><span /></span></div>
      </div>
    </div>

    <div class="chat-input-bar">
      <div class="input-wrapper">
        <textarea
          v-model="inputText"
          rows="1"
          :placeholder="inputPlaceholder"
          :disabled="isStreaming"
          @keydown.enter.exact="onEnter"
        />
        <button
          class="btn-send"
          :disabled="!canSend"
          @click="$emit('send')"
        >
          &#10148;
        </button>
      </div>
    </div>
  </main>
</template>

<script setup>
import GoldenQuestionReviewPanel from '../ontology/GoldenQuestionReviewPanel.vue'
import MarkdownContent from '../../shared/ui/MarkdownContent.vue'

const inputText = defineModel('inputText', {
  type: String,
  default: '',
})

const props = defineProps({
  examples: {
    type: Array,
    required: true,
  },
  canSend: {
    type: Boolean,
    required: true,
  },
  canSubmitBuildFeedback: {
    type: Function,
    required: true,
  },
  inputPlaceholder: {
    type: String,
    required: true,
  },
  isNeo4jTool: {
    type: Function,
    required: true,
  },
  isStreaming: {
    type: Boolean,
    required: true,
  },
  messages: {
    type: Array,
    required: true,
  },
  modeDescription: {
    type: String,
    required: true,
  },
  modeLabel: {
    type: String,
    required: true,
  },
})

const emit = defineEmits(['download', 'send', 'send-example', 'submit-build-feedback'])

function setBuildVerdict(message, index, verdict) {
  const item = message?.buildReport?.goldenQuestions?.[index]
  if (!item) {
    return
  }
  item.verdict = item.verdict === verdict ? null : verdict
  if (item.verdict !== 'incorrect') {
    item.feedback = ''
  }
}

function updateBuildFeedback(message, index, value) {
  const item = message?.buildReport?.goldenQuestions?.[index]
  if (!item) {
    return
  }
  item.feedback = value
}

function getToolGroups(msg) {
  const groups = []
  for (const step of msg.steps) {
    if (step.type === 'tool_start') {
      groups.push({ name: step.name, done: step.done, result: null })
    } else if (step.type === 'tool_result') {
      for (let i = groups.length - 1; i >= 0; i--) {
        if (groups[i].name === step.name && !groups[i].result) {
          groups[i].result = step.content
          break
        }
      }
    }
  }
  return groups
}

function getTextContent(msg) {
  return msg.steps
    .filter((s) => s.type === 'token')
    .map((s) => s.text)
    .join('')
}

function hasRunningTools(msg) {
  return msg.steps.some((s) => s.type === 'tool_start' && !s.done)
}

function getToolSummaryLabel(msg) {
  const groups = getToolGroups(msg)
  const running = groups.filter((g) => !g.done)
  if (running.length) {
    return `${running[running.length - 1].name} 실행 중...`
  }
  return `${groups.length}개 도구 사용됨`
}

function onEnter(event) {
  if (!event.shiftKey) {
    event.preventDefault()
    if (props.canSend) {
      emit('send')
    }
  }
}
</script>
