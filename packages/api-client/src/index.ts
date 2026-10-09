export * from './types.js';
export * from './client.js';
export * from './mock.js';
export * from './formatters.js';
export * from './retentions.js';

import { ICoreApiClient, IFiscalApiClient, HttpCoreApiClient, HttpFiscalApiClient, AuthMemoryStore } from './client.js';
import { MockCoreApiClient, MockFiscalApiClient } from './mock.js';

export interface ApiClientConfig {
  useMock?: boolean;
  coreBaseUrl?: string;
  fiscalBaseUrl?: string;
}

export interface ApiClients {
  core: ICoreApiClient;
  fiscal: IFiscalApiClient;
  isMock: boolean;
}

let activeClients: ApiClients | null = null;

export function getApiClients(config: ApiClientConfig = {}): ApiClients {
  // Por padrão usa cliente real HTTP a menos que explicitamente solicitado mock
  const shouldUseMock = config.useMock !== undefined ? config.useMock : false;

  if (activeClients && activeClients.isMock === shouldUseMock) {
    return activeClients;
  }

  if (shouldUseMock) {
    activeClients = {
      core: new MockCoreApiClient(),
      fiscal: new MockFiscalApiClient(),
      isMock: true,
    };
  } else {
    const core = new HttpCoreApiClient(config.coreBaseUrl || '/api/core/v1');
    const fiscal = new HttpFiscalApiClient(
      config.fiscalBaseUrl || '/api/fiscal/v1',
      AuthMemoryStore.getInstance(),
      core
    );
    activeClients = {
      core,
      fiscal,
      isMock: false,
    };
  }

  return activeClients;
}
