export {};

declare global {
  interface Window {
    google: { accounts: { oauth2: {
      initTokenClient(config: TeacherTokenClientConfig): TeacherTokenClient;
    } } };
  }
  interface TeacherTokenClientConfig {
    client_id: string;
    scope: string;
    callback: (response: TeacherTokenResponse) => void;
    error_callback?: (error: TeacherTokenError) => void;
  }
  interface TeacherTokenClient {
    requestAccessToken(params?: { scope?: string; include_granted_scopes?: boolean; prompt?: string }): void;
  }
  interface TeacherTokenResponse {
    access_token: string;
    expires_in: number;
    scope?: string;
    token_type: string;
  }
  interface TeacherTokenError { error: string; type?: string; error_description?: string; }
}
