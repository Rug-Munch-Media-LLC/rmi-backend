# HealthApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**healthHealthGet**](HealthApi.md#healthhealthget) | **GET** /health | Health |
| [**liveLiveGet**](HealthApi.md#liveliveget) | **GET** /live | Live |
| [**readyReadyGet**](HealthApi.md#readyreadyget) | **GET** /ready | Ready |



## healthHealthGet

> any healthHealthGet()

Health

Deep health — runs every registered domain health check.  Each domain registers its own check via core.health.register_health_check(). Returns 200 if all domains healthy, 503 if any critical domain down.

### Example

```ts
import {
  Configuration,
  HealthApi,
} from '@rugmunch/sdk';
import type { HealthHealthGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new HealthApi();

  try {
    const data = await api.healthHealthGet();
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

**any**

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


## liveLiveGet

> { [key: string]: any; } liveLiveGet()

Live

Liveness probe. Process up. Always 200 unless the process is dead.

### Example

```ts
import {
  Configuration,
  HealthApi,
} from '@rugmunch/sdk';
import type { LiveLiveGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new HealthApi();

  try {
    const data = await api.liveLiveGet();
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


## readyReadyGet

> any readyReadyGet()

Ready

Readiness probe. 200 if critical deps reachable, 503 otherwise.

### Example

```ts
import {
  Configuration,
  HealthApi,
} from '@rugmunch/sdk';
import type { ReadyReadyGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new HealthApi();

  try {
    const data = await api.readyReadyGet();
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

**any**

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

