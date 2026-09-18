import type { ChatAction, ChatState } from "../../state/chatTypes";
import type { ChatMessage } from "../../types/chat";

export type PlansSliceState = Pick<ChatState, "messagesBySession">;
export type PlansSliceUpdate = Pick<ChatState, "messagesBySession">;

/**
 * Pure update of the plan cards carried inside conversation messages: plan
 * creation, status transitions and plan errors. Returns null when the action is
 * not a plan action.
 */
export function reducePlans(
  state: PlansSliceState,
  action: ChatAction,
): PlansSliceUpdate | null {
  if (action.type === "planReady") {
    const { payload, messageId } = action;
    const nextMessage: ChatMessage = {
      id: messageId,
      session_id: payload.session_id,
      role: "agent",
      text: payload.content,
      kind: "plan",
      plan_id: payload.plan_id,
      plan_status: "pending",
    };
    return updateSessionMessages(state, payload.session_id, (messages) =>
      messages.some((message) => message.id === messageId)
        ? messages.map((message) =>
            message.id === messageId ? { ...message, ...nextMessage } : message,
          )
        : [...messages, nextMessage],
    );
  }

  if (action.type === "planStatusChanged") {
    return updateSessionMessages(state, action.sessionId, (messages) =>
      messages.map((message) =>
        message.plan_id === action.planId
          ? { ...message, plan_status: action.status }
          : message,
      ),
    );
  }

  if (action.type === "currentPlanError") {
    return updateSessionMessages(state, action.sessionId, (messages) =>
      messages.map((message) => {
        if (action.planId && message.plan_id === action.planId) {
          return { ...message, plan_status: "failed" };
        }
        if (
          !action.planId &&
          message.kind === "plan" &&
          (message.plan_status === "pending" ||
            message.plan_status === "approving" ||
            message.plan_status === "executing")
        ) {
          return { ...message, plan_status: "failed" };
        }
        return message;
      }),
    );
  }

  return null;
}

function updateSessionMessages(
  state: PlansSliceState,
  sessionId: string,
  update: (messages: ChatMessage[]) => ChatMessage[],
): PlansSliceUpdate {
  return {
    messagesBySession: {
      ...state.messagesBySession,
      [sessionId]: update(state.messagesBySession[sessionId] ?? []),
    },
  };
}
