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
            <div v-if="msg.files && msg.files.length" class="msg-files">
              <span v-for="file in msg.files" :key="file" class="msg-file-chip">{{ file }}</span>
            </div>
          </div>
        </div>

        <div v-else-if="msg.role === 'assistant'" class="msg msg-assistant">
          <div class="msg-avatar">AI</div>
          <div class="msg-body">
            <template v-for="(step, stepIndex) in msg.steps" :key="stepIndex">
              <div v-if="step.type === 'token'" class="ai-text">{{ step.text }}</div>
              <div
                v-else-if="step.type === 'tool_start'"
                class="tool-block tool-start"
                :class="{ 'tool-neo4j': isNeo4jTool(step.name) }"
              >
                <span class="tool-badge" :class="{ 'badge-neo4j': isNeo4jTool(step.name) }">
                  {{ isNeo4jTool(step.name) ? 'NEO4J' : 'TOOL' }}
                </span>
                <code>{{ step.name }}</code>
                <span v-if="!step.done" class="tool-running">실행 중...</span>
              </div>
              <div v-else-if="step.type === 'tool_result'" class="tool-block tool-result">
                <details>
                  <summary><code>{{ step.name }}</code> 결과</summary>
                  <pre>{{ step.content }}</pre>
                </details>
              </div>
            </template>

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
          :disabled="isStreaming || !inputText.trim()"
          @click="$emit('send')"
        >
          &#10148;
        </button>
      </div>
    </div>
  </main>
</template>

<script setup>
const inputText = defineModel('inputText', {
  type: String,
  default: '',
})

defineProps({
  examples: {
    type: Array,
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

const emit = defineEmits(['download', 'send', 'send-example'])

function onEnter(event) {
  if (!event.shiftKey) {
    event.preventDefault()
    emit('send')
  }
}
</script>
