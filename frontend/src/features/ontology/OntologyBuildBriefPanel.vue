<template>
  <section class="build-brief-card">
    <div class="build-brief-header">
      <div>
        <h3>구축 브리프</h3>
        <p>먼저 왜 이 온톨로지를 구축하는지와 반드시 답해야 하는 Golden Question을 정의하세요.</p>
      </div>
      <button class="btn-build-start" :disabled="!canStart || isStreaming" @click="$emit('start-build')">
        이 브리프로 구축 시작
      </button>
    </div>

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
        <button class="btn-inline-add" :disabled="isStreaming" @click="$emit('add-question')">
          질문 추가
        </button>
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

    <div class="build-brief-footer">
      <div class="build-brief-validation" :class="{ ready: canStart }">
        <strong>{{ canStart ? '준비 완료' : '입력 필요' }}</strong>
        <span>의도 1개와 Golden Question 최소 1개가 있어야 구축을 시작할 수 있습니다.</span>
      </div>
    </div>
  </section>
</template>

<script setup>
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

defineEmits([
  'add-question',
  'remove-question',
  'start-build',
  'update:intent',
  'update:question',
])
</script>
