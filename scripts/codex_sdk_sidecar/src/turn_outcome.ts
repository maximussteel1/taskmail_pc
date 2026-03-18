export type TurnOutcomeState = {
  completed: boolean;
  failureMessage: string | null;
};

export function createTurnOutcomeState(): TurnOutcomeState {
  return {
    completed: false,
    failureMessage: null,
  };
}

export function noteTurnCompleted(_state: TurnOutcomeState): TurnOutcomeState {
  return {
    completed: true,
    failureMessage: null,
  };
}

export function noteTurnFailure(state: TurnOutcomeState, message: string | null | undefined): TurnOutcomeState {
  const normalized = typeof message === "string" ? message.trim() : "";
  if (!normalized || state.completed) {
    return state;
  }
  return {
    completed: false,
    failureMessage: normalized,
  };
}

export function terminalTurnFailureMessage(state: TurnOutcomeState): string | null {
  if (state.completed) {
    return null;
  }
  return state.failureMessage;
}
