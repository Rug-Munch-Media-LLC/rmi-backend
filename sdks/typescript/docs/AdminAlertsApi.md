# AdminAlertsApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**alertsCriticalWebhookApiV1AdminAlertsCriticalPost**](AdminAlertsApi.md#alertscriticalwebhookapiv1adminalertscriticalpost) | **POST** /api/v1/admin/alerts/critical | Alerts Critical Webhook |
| [**alertsRecentApiV1AdminAlertsRecentGet**](AdminAlertsApi.md#alertsrecentapiv1adminalertsrecentget) | **GET** /api/v1/admin/alerts/recent | Alerts Recent |
| [**alertsWebhookApiV1AdminAlertsWebhookPost**](AdminAlertsApi.md#alertswebhookapiv1adminalertswebhookpost) | **POST** /api/v1/admin/alerts/webhook | Alerts Webhook |



## alertsCriticalWebhookApiV1AdminAlertsCriticalPost

> { [key: string]: any; } alertsCriticalWebhookApiV1AdminAlertsCriticalPost(alertmanagerPayload)

Alerts Critical Webhook

Critical-only webhook — escalations only (severity&#x3D;critical).

### Example

```ts
import {
  Configuration,
  AdminAlertsApi,
} from '@rugmunch/sdk';
import type { AlertsCriticalWebhookApiV1AdminAlertsCriticalPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AdminAlertsApi();

  const body = {
    // AlertmanagerPayload
    alertmanagerPayload: ...,
  } satisfies AlertsCriticalWebhookApiV1AdminAlertsCriticalPostRequest;

  try {
    const data = await api.alertsCriticalWebhookApiV1AdminAlertsCriticalPost(body);
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
| **alertmanagerPayload** | [AlertmanagerPayload](AlertmanagerPayload.md) |  | |

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


## alertsRecentApiV1AdminAlertsRecentGet

> { [key: string]: any; } alertsRecentApiV1AdminAlertsRecentGet(limit)

Alerts Recent

Read recent alerts (debug endpoint — admin only in production).

### Example

```ts
import {
  Configuration,
  AdminAlertsApi,
} from '@rugmunch/sdk';
import type { AlertsRecentApiV1AdminAlertsRecentGetRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AdminAlertsApi();

  const body = {
    // number (optional)
    limit: 56,
  } satisfies AlertsRecentApiV1AdminAlertsRecentGetRequest;

  try {
    const data = await api.alertsRecentApiV1AdminAlertsRecentGet(body);
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
| **limit** | `number` |  | [Optional] [Defaults to `50`] |

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


## alertsWebhookApiV1AdminAlertsWebhookPost

> { [key: string]: any; } alertsWebhookApiV1AdminAlertsWebhookPost(alertmanagerPayload)

Alerts Webhook

Default receiver webhook — all severities.

### Example

```ts
import {
  Configuration,
  AdminAlertsApi,
} from '@rugmunch/sdk';
import type { AlertsWebhookApiV1AdminAlertsWebhookPostRequest } from '@rugmunch/sdk';

async function example() {
  console.log("🚀 Testing @rugmunch/sdk SDK...");
  const api = new AdminAlertsApi();

  const body = {
    // AlertmanagerPayload
    alertmanagerPayload: ...,
  } satisfies AlertsWebhookApiV1AdminAlertsWebhookPostRequest;

  try {
    const data = await api.alertsWebhookApiV1AdminAlertsWebhookPost(body);
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
| **alertmanagerPayload** | [AlertmanagerPayload](AlertmanagerPayload.md) |  | |

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

