# TokenScanRequest

Request body for the token scan endpoint.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | [optional] [default to 'solana']
**tier** | **str** |  | [optional] [default to 'free']

## Example

```python
from rugmunch.models.token_scan_request import TokenScanRequest

# TODO update the JSON string below
json = "{}"
# create an instance of TokenScanRequest from a JSON string
token_scan_request_instance = TokenScanRequest.from_json(json)
# print the JSON string representation of the object
print(TokenScanRequest.to_json())

# convert the object into a dict
token_scan_request_dict = token_scan_request_instance.to_dict()
# create an instance of TokenScanRequest from a dict
token_scan_request_from_dict = TokenScanRequest.from_dict(token_scan_request_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


