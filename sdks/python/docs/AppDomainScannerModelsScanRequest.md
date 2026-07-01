# AppDomainScannerModelsScanRequest

Request body for the token scan endpoint.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** | Token contract address | 
**chain** | **str** |  | [optional] [default to 'solana']
**tier** | [**ScanTier**](ScanTier.md) |  | [optional] 
**user_id** | **str** | Optional user id for rate-limit tracking | [optional] 

## Example

```python
from rugmunch.models.app_domain_scanner_models_scan_request import AppDomainScannerModelsScanRequest

# TODO update the JSON string below
json = "{}"
# create an instance of AppDomainScannerModelsScanRequest from a JSON string
app_domain_scanner_models_scan_request_instance = AppDomainScannerModelsScanRequest.from_json(json)
# print the JSON string representation of the object
print(AppDomainScannerModelsScanRequest.to_json())

# convert the object into a dict
app_domain_scanner_models_scan_request_dict = app_domain_scanner_models_scan_request_instance.to_dict()
# create an instance of AppDomainScannerModelsScanRequest from a dict
app_domain_scanner_models_scan_request_from_dict = AppDomainScannerModelsScanRequest.from_dict(app_domain_scanner_models_scan_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


