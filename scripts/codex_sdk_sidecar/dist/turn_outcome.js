export function createTurnOutcomeState() {
    return {
        completed: false,
        failureMessage: null,
    };
}
export function noteTurnCompleted(_state) {
    return {
        completed: true,
        failureMessage: null,
    };
}
export function noteTurnFailure(state, message) {
    const normalized = typeof message === "string" ? message.trim() : "";
    if (!normalized || state.completed) {
        return state;
    }
    return {
        completed: false,
        failureMessage: normalized,
    };
}
export function terminalTurnFailureMessage(state) {
    if (state.completed) {
        return null;
    }
    return state.failureMessage;
}
