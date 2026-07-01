# CatalogApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**attachRagApiV1CatalogTokensChainAddressAttachRagPost**](CatalogApi.md#attachragapiv1catalogtokenschainaddressattachragpost) | **POST** /api/v1/catalog/tokens/{chain}/{address}/attach-rag | Attach Rag |
| [**getTokenApiV1CatalogTokensChainAddressGet**](CatalogApi.md#gettokenapiv1catalogtokenschainaddressget) | **GET** /api/v1/catalog/tokens/{chain}/{address} | Get Token |
| [**getTokenRiskApiV1CatalogTokensChainAddressRiskGet**](CatalogApi.md#gettokenriskapiv1catalogtokenschainaddressriskget) | **GET** /api/v1/catalog/tokens/{chain}/{address}/risk | Get Token Risk |
| [**getWalletApiV1CatalogWalletsChainAddressGet**](CatalogApi.md#getwalletapiv1catalogwalletschainaddressget) | **GET** /api/v1/catalog/wallets/{chain}/{address} | Get Wallet |
| [**probeApiV1CatalogProbeGet**](CatalogApi.md#probeapiv1catalogprobeget) | **GET** /api/v1/catalog/probe | Probe |
| [**ragIngestApiV1CatalogRagIngestPost**](CatalogApi.md#ragingestapiv1catalogragingestpost) | **POST** /api/v1/catalog/rag/ingest | Rag Ingest |
| [**ragSearchApiV1CatalogRagSearchPost**](CatalogApi.md#ragsearchapiv1catalogragsearchpost) | **POST** /api/v1/catalog/rag/search | Rag Search |
| [**resolveEntityApiV1CatalogEntitiesResolvePost**](CatalogApi.md#resolveentityapiv1catalogentitiesresolvepost) | **POST** /api/v1/catalog/entities/resolve | Resolve Entity |
| [**riskyTokensApiV1CatalogTokensRiskyByDeployerPost**](CatalogApi.md#riskytokensapiv1catalogtokensriskybydeployerpost) | **POST** /api/v1/catalog/tokens/risky-by-deployer | Risky Tokens |
| [**statsApiV1CatalogStatsGet**](CatalogApi.md#statsapiv1catalogstatsget) | **GET** /api/v1/catalog/stats | Stats |



## attachRagApiV1CatalogTokensChainAddressAttachRagPost

> { [key: string]: any; } attachRagApiV1CatalogTokensChainAddressAttachRagPost(chain, address, attachRagRequest)

Attach Rag

Link an existing RAG embedding (Qdrant point) to a Token row.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { AttachRagApiV1CatalogTokensChainAddressAttachRagPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // string
    chain: chain_example,
    // string
    address: address_example,
    // AttachRagRequest
    attachRagRequest: ...,
  } satisfies AttachRagApiV1CatalogTokensChainAddressAttachRagPostRequest;

  try {
    const data = await api.attachRagApiV1CatalogTokensChainAddressAttachRagPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **chain** | `string` |  | [Defaults to `undefined`] |
| **address** | `string` |  | [Defaults to `undefined`] |
| **attachRagRequest** | [AttachRagRequest](AttachRagRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getTokenApiV1CatalogTokensChainAddressGet

> { [key: string]: any; } getTokenApiV1CatalogTokensChainAddressGet(chain, address)

Get Token

Get a token by chain+address. Returns full Token model + provenance.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { GetTokenApiV1CatalogTokensChainAddressGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // string
    chain: chain_example,
    // string
    address: address_example,
  } satisfies GetTokenApiV1CatalogTokensChainAddressGetRequest;

  try {
    const data = await api.getTokenApiV1CatalogTokensChainAddressGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **chain** | `string` |  | [Defaults to `undefined`] |
| **address** | `string` |  | [Defaults to `undefined`] |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getTokenRiskApiV1CatalogTokensChainAddressRiskGet

> { [key: string]: any; } getTokenRiskApiV1CatalogTokensChainAddressRiskGet(chain, address)

Get Token Risk

Recipe 3 — Real-time risk score. Composes Redis + Postgres + Neo4j.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { GetTokenRiskApiV1CatalogTokensChainAddressRiskGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // string
    chain: chain_example,
    // string
    address: address_example,
  } satisfies GetTokenRiskApiV1CatalogTokensChainAddressRiskGetRequest;

  try {
    const data = await api.getTokenRiskApiV1CatalogTokensChainAddressRiskGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **chain** | `string` |  | [Defaults to `undefined`] |
| **address** | `string` |  | [Defaults to `undefined`] |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getWalletApiV1CatalogWalletsChainAddressGet

> { [key: string]: any; } getWalletApiV1CatalogWalletsChainAddressGet(chain, address)

Get Wallet

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { GetWalletApiV1CatalogWalletsChainAddressGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // string
    chain: chain_example,
    // string
    address: address_example,
  } satisfies GetWalletApiV1CatalogWalletsChainAddressGetRequest;

  try {
    const data = await api.getWalletApiV1CatalogWalletsChainAddressGet(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **chain** | `string` |  | [Defaults to `undefined`] |
| **address** | `string` |  | [Defaults to `undefined`] |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## probeApiV1CatalogProbeGet

> { [key: string]: any; } probeApiV1CatalogProbeGet()

Probe

Probe which stores are reachable from this container.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { ProbeApiV1CatalogProbeGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  try {
    const data = await api.probeApiV1CatalogProbeGet();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## ragIngestApiV1CatalogRagIngestPost

> { [key: string]: any; } ragIngestApiV1CatalogRagIngestPost(ragIngestRequest)

Rag Ingest

Ingest content into RAG. Returns qdrant_point_id for cross-store linking.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { RagIngestApiV1CatalogRagIngestPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // RagIngestRequest
    ragIngestRequest: ...,
  } satisfies RagIngestApiV1CatalogRagIngestPostRequest;

  try {
    const data = await api.ragIngestApiV1CatalogRagIngestPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **ragIngestRequest** | [RagIngestRequest](RagIngestRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## ragSearchApiV1CatalogRagSearchPost

> { [key: string]: any; } ragSearchApiV1CatalogRagSearchPost(ragSearchRequest)

Rag Search

Search the RAG system. Returns ranked hits with RRF scores.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { RagSearchApiV1CatalogRagSearchPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // RagSearchRequest
    ragSearchRequest: ...,
  } satisfies RagSearchApiV1CatalogRagSearchPostRequest;

  try {
    const data = await api.ragSearchApiV1CatalogRagSearchPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **ragSearchRequest** | [RagSearchRequest](RagSearchRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## resolveEntityApiV1CatalogEntitiesResolvePost

> { [key: string]: any; } resolveEntityApiV1CatalogEntitiesResolvePost(resolveEntityRequest)

Resolve Entity

Cross-chain entity resolution via Neo4j Cypher.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { ResolveEntityApiV1CatalogEntitiesResolvePostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // ResolveEntityRequest
    resolveEntityRequest: ...,
  } satisfies ResolveEntityApiV1CatalogEntitiesResolvePostRequest;

  try {
    const data = await api.resolveEntityApiV1CatalogEntitiesResolvePost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **resolveEntityRequest** | [ResolveEntityRequest](ResolveEntityRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## riskyTokensApiV1CatalogTokensRiskyByDeployerPost

> { [key: string]: any; } riskyTokensApiV1CatalogTokensRiskyByDeployerPost(findRiskyTokensRequest)

Risky Tokens

Recipe 1 — Find tokens deployed by wallets with rug history.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { RiskyTokensApiV1CatalogTokensRiskyByDeployerPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  const body = {
    // FindRiskyTokensRequest
    findRiskyTokensRequest: ...,
  } satisfies RiskyTokensApiV1CatalogTokensRiskyByDeployerPostRequest;

  try {
    const data = await api.riskyTokensApiV1CatalogTokensRiskyByDeployerPost(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **findRiskyTokensRequest** | [FindRiskyTokensRequest](FindRiskyTokensRequest.md) |  | |

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## statsApiV1CatalogStatsGet

> { [key: string]: any; } statsApiV1CatalogStatsGet()

Stats

Catalog stats: which stores are reachable + entity counts.

### Example

```ts
import {
  Configuration,
  CatalogApi,
} from '@rugmunch/sdk';
import type { StatsApiV1CatalogStatsGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new CatalogApi();

  try {
    const data = await api.statsApiV1CatalogStatsGet();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

**{ [key: string]: any; }**

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

