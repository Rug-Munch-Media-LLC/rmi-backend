# AppDomainWalletModelsScanRequest

Request body for the wallet scan endpoint.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | [optional] [default to 'solana']
**tier** | **str** | free | pro | elite | internal | [optional] [default to 'free']

## Example

```python
from rugmunch.models.app_domain_wallet_models_scan_request import AppDomainWalletModelsScanRequest

# TODO update the JSON string below
json = "{}"
# create an instance of AppDomainWalletModelsScanRequest from a JSON string
app_domain_wallet_models_scan_request_instance = AppDomainWalletModelsScanRequest.from_json(json)
# print the JSON string representation of the object
print(AppDomainWalletModelsScanRequest.to_json())

# convert the object into a dict
app_domain_wallet_models_scan_request_dict = app_domain_wallet_models_scan_request_instance.to_dict()
# create an instance of AppDomainWalletModelsScanRequest from a dict
app_domain_wallet_models_scan_request_from_dict = AppDomainWalletModelsScanRequest.from_dict(app_domain_wallet_models_scan_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


