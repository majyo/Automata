import type { ChatAction, ChatState } from "../../state/chatTypes";
import type { PendingInput } from "../../types/chat";

export type InputsSliceState = Pick<ChatState, "inputsBySession">;
export type InputsSliceUpdate = Pick<ChatState, "inputsBySession">;

/**
 * Pure update of the per-session list of inputs that are waiting to be
 * delivered.
 *
 * A submitted prompt is only a *candidate* message until the backend says
 * what happened to it, so this slice owns the optimistic entry and the
 * acknowledgement that settles or drops it. Nothing here renders a
 * conversation message: a steering input becomes visible through
 * `input_applied`, and a queued input through its Run's `started` event,
 * which keeps the visible message order identical to the durable one.
 *
 * Returns null when the action does not touch inputs so the composing
 * reducer can keep the previous state.
 */
export function reduceInputs(
  state: InputsSliceState,
  action: ChatAction,
): InputsSliceUpdate | null {
  if (action.type === "sessionMessagesCleared") {
    if (!(action.sessionId in state.inputsBySession)) {
      return null;
    }
    const inputsBySession = { ...state.inputsBySession };
    delete inputsBySession[action.sessionId];
    return { inputsBySession };
  }

  if (action.type === "messagesLoaded") {
    // A persisted message carrying the input id means the input has been
    // delivered, which is how a queue entry left over from a reload or a
    // reconnect stops being shown as waiting.
    const delivered = new Set<string>();
    for (const message of action.messages) {
      const inputId = message.metadata?.input_id;
      if (inputId) {
        delivered.add(inputId);
      }
    }
    if (delivered.size === 0) {
      return null;
    }
    return dropSessionInputs(
      state,
      action.sessionId,
      (input) => input.inputId !== undefined && delivered.has(input.inputId),
    );
  }

  if (action.type === "inputSubmitted") {
    const entry: PendingInput = {
      requestId: action.requestId,
      sessionId: action.sessionId,
      prompt: action.prompt,
      delivery: action.delivery,
      status: "sending",
      runId: action.runId ?? null,
    };
    return updateSessionInputs(state, action.sessionId, (inputs) => [
      ...inputs.filter((input) => input.requestId !== action.requestId),
      entry,
    ]);
  }

  if (action.type === "inputAccepted") {
    return updateMatching(state, action.sessionId, (input) => {
      if (input.requestId !== action.requestId) {
        return input;
      }
      return {
        ...input,
        status: "pending",
        inputId: action.inputId ?? input.inputId,
        position: action.position,
        runId: action.runId ?? input.runId,
      };
    });
  }

  if (action.type === "inputCancelling") {
    return updateMatching(state, action.sessionId, (input) =>
      input.inputId === action.inputId ? { ...input, status: "cancelling" } : input,
    );
  }

  if (action.type === "inputSteerRequested") {
    return updateMatching(state, action.sessionId, (input) =>
      input.inputId === action.inputId
        ? { ...input, steerRequestId: action.requestId }
        : input,
    );
  }

  if (action.type === "inputSteerFailed") {
    return updateMatching(state, action.sessionId, (input) =>
      input.steerRequestId === action.requestId
        ? { ...input, steerRequestId: undefined }
        : input,
    );
  }

  if (action.type === "inputCancelled") {
    return dropSessionInputs(state, action.sessionId, (input) =>
      action.inputId ? input.inputId === action.inputId : false,
    );
  }

  if (action.type === "inputMaterialized") {
    return dropSessionInputs(
      state,
      action.sessionId,
      (input) => input.inputId === action.inputId,
    );
  }

  if (action.type === "inputFailed") {
    return dropSessionInputs(
      state,
      action.sessionId,
      (input) =>
        (action.requestId !== undefined && input.requestId === action.requestId) ||
        (action.inputId !== undefined && input.inputId === action.inputId),
    );
  }

  return null;
}

function updateMatching(
  state: InputsSliceState,
  sessionId: string,
  update: (input: PendingInput) => PendingInput,
): InputsSliceUpdate | null {
  const inputs = state.inputsBySession[sessionId];
  if (!inputs || inputs.length === 0) {
    return null;
  }
  const updated = inputs.map(update);
  const changed = updated.some((input, index) => input !== inputs[index]);
  if (!changed) {
    return null;
  }
  return {
    inputsBySession: { ...state.inputsBySession, [sessionId]: updated },
  };
}

function dropSessionInputs(
  state: InputsSliceState,
  sessionId: string,
  matches: (input: PendingInput) => boolean,
): InputsSliceUpdate | null {
  const inputs = state.inputsBySession[sessionId];
  if (!inputs || inputs.length === 0) {
    return null;
  }
  const kept = inputs.filter((input) => !matches(input));
  if (kept.length === inputs.length) {
    return null;
  }
  return {
    inputsBySession: { ...state.inputsBySession, [sessionId]: kept },
  };
}

function updateSessionInputs(
  state: InputsSliceState,
  sessionId: string,
  update: (inputs: PendingInput[]) => PendingInput[],
): InputsSliceUpdate {
  return {
    inputsBySession: {
      ...state.inputsBySession,
      [sessionId]: update(state.inputsBySession[sessionId] ?? []),
    },
  };
}
