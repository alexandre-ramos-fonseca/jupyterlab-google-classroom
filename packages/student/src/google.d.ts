// Tipos mínimos do Google Identity Services (apenas o fluxo de token).
// Não adiciona dependência npm; o script é carregado de
// https://accounts.google.com/gsi/client em runtime.

export {};

declare global {
  interface Window {
    google: {
      accounts: {
        oauth2: {
          initTokenClient(config: GsiTokenClientConfig): GsiTokenClient;
          hasGrantedAllScopes(response: GsiTokenResponse, ...scopes: string[]): boolean;
        };
      };
    };
  }

  interface GsiTokenClientConfig {
    client_id: string;
    scope?: string;
    callback: (response: GsiTokenResponse) => void;
    error_callback?: (error: GsiTokenError) => void;
  }

  interface GsiTokenClient {
    requestAccessToken(overridableParams?: {
      scope?: string;
      include_granted_scopes?: boolean;
      prompt?: string;
    }): void;
  }

  interface GsiTokenResponse {
    access_token: string;
    expires_in: number;
    scope: string;
    token_type: string;
  }

  interface GsiTokenError {
    error: string;
    type?: string;
    error_description?: string;
  }
}
