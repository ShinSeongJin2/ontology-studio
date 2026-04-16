<template>
  <section class="build-brief-main">
    <div class="build-brief-content">
      <div class="build-brief-center">
        <div class="build-brief-welcome">
          <h1 class="build-brief-title">Ontology Studio</h1>
          <div class="welcome-mode">온톨로지 구축</div>
          <p class="build-brief-desc">
            구축 의도와 반드시 답해야 하는 Golden Question을 정의한 뒤, 구축을 시작하세요.
          </p>
        </div>

        <div class="build-brief-form">
          <label class="build-brief-field">
            <span>구축 의도</span>
            <textarea
              :value="intent"
              :disabled="isStreaming"
              rows="3"
              placeholder="예: 내부 감사 담당자가 문서의 핵심 개체와 관계를 빠르게 추적할 수 있도록 온톨로지를 만들고 싶습니다."
              @input="$emit('update:intent', $event.target.value)"
            />
          </label>

          <div class="build-brief-field">
            <div class="build-brief-field-header">
              <span>Golden Question</span>
              <div class="build-brief-field-actions">
                <button class="btn-inline-add" :disabled="isStreaming" @click="triggerFileUpload">
                  파일에서 불러오기
                </button>
                <button class="btn-inline-add" :disabled="isStreaming" @click="$emit('add-question')">
                  질문 추가
                </button>
              </div>
              <input
                ref="fileInputRef"
                type="file"
                accept=".txt,.csv,.tsv"
                hidden
                @change="onFileSelected"
              />
            </div>

            <div class="golden-question-list">
              <div
                v-for="(question, index) in goldenQuestions"
                :key="`golden-question-${index}`"
                class="golden-question-item"
              >
                <input
                  :value="question"
                  :disabled="isStreaming"
                  type="text"
                  :placeholder="`예: Golden Question ${index + 1}`"
                  @input="$emit('update:question', index, $event.target.value)"
                />
                <button
                  class="btn-inline-remove"
                  :disabled="isStreaming"
                  @click="$emit('remove-question', index)"
                >
                  삭제
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="build-brief-action-bar">
      <div class="build-brief-action-inner">
        <p v-if="!canStart" class="build-brief-hint">
          파일 1개 이상 업로드, 의도 1개, Golden Question 최소 1개를 입력하면 구축을 시작할 수 있습니다.
        </p>
        <button
          class="btn-build-start"
          :disabled="!canStart || isStreaming"
          @click="$emit('start-build')"
        >
          이 브리프로 구축 시작
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  intent: {
    type: String,
    required: true,
  },
  goldenQuestions: {
    type: Array,
    required: true,
  },
  canStart: {
    type: Boolean,
    required: true,
  },
  isStreaming: {
    type: Boolean,
    required: true,
  },
})

const emit = defineEmits([
  'add-question',
  'import-questions',
  'remove-question',
  'start-build',
  'update:intent',
  'update:question',
])

const fileInputRef = ref(null)

function triggerFileUpload() {
  fileInputRef.value?.click()
}

function onFileSelected(event) {
  const file = event.target.files?.[0]
  if (!file) return

  const reader = new FileReader()
  reader.onload = (e) => {
    const lines = e.target.result
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
    if (lines.length > 0) {
      emit('import-questions', lines)
    }
  }
  reader.readAsText(file)
  event.target.value = ''
}
</script>
