# rugmunch.CatalogApi

All URIs are relative to *http://localhost*

Method | HTTP request | Description
------------- | ------------- | -------------
[**attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post**](CatalogApi.md#attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post) | **POST** /api/v1/catalog/tokens/{chain}/{address}/attach-rag | Attach Rag
[**get_token_api_v1_catalog_tokens_chain_address_get**](CatalogApi.md#get_token_api_v1_catalog_tokens_chain_address_get) | **GET** /api/v1/catalog/tokens/{chain}/{address} | Get Token
[**get_token_risk_api_v1_catalog_tokens_chain_address_risk_get**](CatalogApi.md#get_token_risk_api_v1_catalog_tokens_chain_address_risk_get) | **GET** /api/v1/catalog/tokens/{chain}/{address}/risk | Get Token Risk
[**get_wallet_api_v1_catalog_wallets_chain_address_get**](CatalogApi.md#get_wallet_api_v1_catalog_wallets_chain_address_get) | **GET** /api/v1/catalog/wallets/{chain}/{address} | Get Wallet
[**probe_api_v1_catalog_probe_get**](CatalogApi.md#probe_api_v1_catalog_probe_get) | **GET** /api/v1/catalog/probe | Probe
[**rag_ingest_api_v1_catalog_rag_ingest_post**](CatalogApi.md#rag_ingest_api_v1_catalog_rag_ingest_post) | **POST** /api/v1/catalog/rag/ingest | Rag Ingest
[**rag_search_api_v1_catalog_rag_search_post**](CatalogApi.md#rag_search_api_v1_catalog_rag_search_post) | **POST** /api/v1/catalog/rag/search | Rag Search
[**resolve_entity_api_v1_catalog_entities_resolve_post**](CatalogApi.md#resolve_entity_api_v1_catalog_entities_resolve_post) | **POST** /api/v1/catalog/entities/resolve | Resolve Entity
[**risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post**](CatalogApi.md#risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post) | **POST** /api/v1/catalog/tokens/risky-by-deployer | Risky Tokens
[**stats_api_v1_catalog_stats_get**](CatalogApi.md#stats_api_v1_catalog_stats_get) | **GET** /api/v1/catalog/stats | Stats


# **attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post**
> Dict[str, object] attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post(chain, address, attach_rag_request)

Attach Rag

Link an existing RAG embedding (Qdrant point) to a Token row.

### Example


```python
import rugmunch
from rugmunch.models.attach_rag_request import AttachRagRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    chain = 'chain_example' # str | 
    address = 'address_example' # str | 
    attach_rag_request = rugmunch.AttachRagRequest() # AttachRagRequest | 

    try:
        # Attach Rag
        api_response = api_instance.attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post(chain, address, attach_rag_request)
        print("The response of CatalogApi->attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->attach_rag_api_v1_catalog_tokens_chain_address_attach_rag_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **chain** | **str**|  | 
 **address** | **str**|  | 
 **attach_rag_request** | [**AttachRagRequest**](AttachRagRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_token_api_v1_catalog_tokens_chain_address_get**
> Dict[str, object] get_token_api_v1_catalog_tokens_chain_address_get(chain, address)

Get Token

Get a token by chain+address. Returns full Token model + provenance.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    chain = 'chain_example' # str | 
    address = 'address_example' # str | 

    try:
        # Get Token
        api_response = api_instance.get_token_api_v1_catalog_tokens_chain_address_get(chain, address)
        print("The response of CatalogApi->get_token_api_v1_catalog_tokens_chain_address_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->get_token_api_v1_catalog_tokens_chain_address_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **chain** | **str**|  | 
 **address** | **str**|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_token_risk_api_v1_catalog_tokens_chain_address_risk_get**
> Dict[str, object] get_token_risk_api_v1_catalog_tokens_chain_address_risk_get(chain, address)

Get Token Risk

Recipe 3 — Real-time risk score. Composes Redis + Postgres + Neo4j.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    chain = 'chain_example' # str | 
    address = 'address_example' # str | 

    try:
        # Get Token Risk
        api_response = api_instance.get_token_risk_api_v1_catalog_tokens_chain_address_risk_get(chain, address)
        print("The response of CatalogApi->get_token_risk_api_v1_catalog_tokens_chain_address_risk_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->get_token_risk_api_v1_catalog_tokens_chain_address_risk_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **chain** | **str**|  | 
 **address** | **str**|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **get_wallet_api_v1_catalog_wallets_chain_address_get**
> Dict[str, object] get_wallet_api_v1_catalog_wallets_chain_address_get(chain, address)

Get Wallet

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    chain = 'chain_example' # str | 
    address = 'address_example' # str | 

    try:
        # Get Wallet
        api_response = api_instance.get_wallet_api_v1_catalog_wallets_chain_address_get(chain, address)
        print("The response of CatalogApi->get_wallet_api_v1_catalog_wallets_chain_address_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->get_wallet_api_v1_catalog_wallets_chain_address_get: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **chain** | **str**|  | 
 **address** | **str**|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **probe_api_v1_catalog_probe_get**
> Dict[str, object] probe_api_v1_catalog_probe_get()

Probe

Probe which stores are reachable from this container.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)

    try:
        # Probe
        api_response = api_instance.probe_api_v1_catalog_probe_get()
        print("The response of CatalogApi->probe_api_v1_catalog_probe_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->probe_api_v1_catalog_probe_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **rag_ingest_api_v1_catalog_rag_ingest_post**
> Dict[str, object] rag_ingest_api_v1_catalog_rag_ingest_post(rag_ingest_request)

Rag Ingest

Ingest content into RAG. Returns qdrant_point_id for cross-store linking.

### Example


```python
import rugmunch
from rugmunch.models.rag_ingest_request import RagIngestRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    rag_ingest_request = rugmunch.RagIngestRequest() # RagIngestRequest | 

    try:
        # Rag Ingest
        api_response = api_instance.rag_ingest_api_v1_catalog_rag_ingest_post(rag_ingest_request)
        print("The response of CatalogApi->rag_ingest_api_v1_catalog_rag_ingest_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->rag_ingest_api_v1_catalog_rag_ingest_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **rag_ingest_request** | [**RagIngestRequest**](RagIngestRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **rag_search_api_v1_catalog_rag_search_post**
> Dict[str, object] rag_search_api_v1_catalog_rag_search_post(rag_search_request)

Rag Search

Search the RAG system. Returns ranked hits with RRF scores.

### Example


```python
import rugmunch
from rugmunch.models.rag_search_request import RagSearchRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    rag_search_request = rugmunch.RagSearchRequest() # RagSearchRequest | 

    try:
        # Rag Search
        api_response = api_instance.rag_search_api_v1_catalog_rag_search_post(rag_search_request)
        print("The response of CatalogApi->rag_search_api_v1_catalog_rag_search_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->rag_search_api_v1_catalog_rag_search_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **rag_search_request** | [**RagSearchRequest**](RagSearchRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **resolve_entity_api_v1_catalog_entities_resolve_post**
> Dict[str, object] resolve_entity_api_v1_catalog_entities_resolve_post(resolve_entity_request)

Resolve Entity

Cross-chain entity resolution via Neo4j Cypher.

### Example


```python
import rugmunch
from rugmunch.models.resolve_entity_request import ResolveEntityRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    resolve_entity_request = rugmunch.ResolveEntityRequest() # ResolveEntityRequest | 

    try:
        # Resolve Entity
        api_response = api_instance.resolve_entity_api_v1_catalog_entities_resolve_post(resolve_entity_request)
        print("The response of CatalogApi->resolve_entity_api_v1_catalog_entities_resolve_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->resolve_entity_api_v1_catalog_entities_resolve_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **resolve_entity_request** | [**ResolveEntityRequest**](ResolveEntityRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post**
> Dict[str, object] risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post(find_risky_tokens_request)

Risky Tokens

Recipe 1 — Find tokens deployed by wallets with rug history.

### Example


```python
import rugmunch
from rugmunch.models.find_risky_tokens_request import FindRiskyTokensRequest
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)
    find_risky_tokens_request = rugmunch.FindRiskyTokensRequest() # FindRiskyTokensRequest | 

    try:
        # Risky Tokens
        api_response = api_instance.risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post(find_risky_tokens_request)
        print("The response of CatalogApi->risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->risky_tokens_api_v1_catalog_tokens_risky_by_deployer_post: %s\n" % e)
```



### Parameters


Name | Type | Description  | Notes
------------- | ------------- | ------------- | -------------
 **find_risky_tokens_request** | [**FindRiskyTokensRequest**](FindRiskyTokensRequest.md)|  | 

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: application/json
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |
**422** | Validation Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

# **stats_api_v1_catalog_stats_get**
> Dict[str, object] stats_api_v1_catalog_stats_get()

Stats

Catalog stats: which stores are reachable + entity counts.

### Example


```python
import rugmunch
from rugmunch.rest import ApiException
from pprint import pprint

# Defining the host is optional and defaults to http://localhost
# See configuration.py for a list of all supported configuration parameters.
configuration = rugmunch.Configuration(
    host = "http://localhost"
)


# Enter a context with an instance of the API client
with rugmunch.ApiClient(configuration) as api_client:
    # Create an instance of the API class
    api_instance = rugmunch.CatalogApi(api_client)

    try:
        # Stats
        api_response = api_instance.stats_api_v1_catalog_stats_get()
        print("The response of CatalogApi->stats_api_v1_catalog_stats_get:\n")
        pprint(api_response)
    except Exception as e:
        print("Exception when calling CatalogApi->stats_api_v1_catalog_stats_get: %s\n" % e)
```



### Parameters

This endpoint does not need any parameter.

### Return type

**Dict[str, object]**

### Authorization

No authorization required

### HTTP request headers

 - **Content-Type**: Not defined
 - **Accept**: application/json

### HTTP response details

| Status code | Description | Response headers |
|-------------|-------------|------------------|
**200** | Successful Response |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to Model list]](../README.md#documentation-for-models) [[Back to README]](../README.md)

