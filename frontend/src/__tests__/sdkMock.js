/** Shared fake for @aws-sdk/client-cognito-identity-provider: every command is recorded; `respond` decides the result. */
export const sdkState = { calls: [], respond: null };

export function cognitoError(name, message = "x") {
  const error = new Error(message);
  error.name = name;
  return error;
}

export function mockSdk() {
  const command = (name) =>
    class {
      constructor(input) {
        this.name = name;
        this.input = input;
      }
    };
  return {
    CognitoIdentityProviderClient: class {
      async send(cmd) {
        sdkState.calls.push({ name: cmd.name, input: cmd.input });
        if (!sdkState.respond) return {};
        return sdkState.respond(cmd.name, cmd.input);
      }
    },
    SignUpCommand: command("SignUpCommand"),
    ConfirmSignUpCommand: command("ConfirmSignUpCommand"),
    ResendConfirmationCodeCommand: command("ResendConfirmationCodeCommand"),
    InitiateAuthCommand: command("InitiateAuthCommand"),
    ForgotPasswordCommand: command("ForgotPasswordCommand"),
    ConfirmForgotPasswordCommand: command("ConfirmForgotPasswordCommand"),
    GlobalSignOutCommand: command("GlobalSignOutCommand"),
  };
}
