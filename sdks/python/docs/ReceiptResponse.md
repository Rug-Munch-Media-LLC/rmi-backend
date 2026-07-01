# ReceiptResponse


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**tx_hash** | **str** |  | 
**tool** | **str** |  | 
**agent_id** | **str** |  | [optional] 
**amount_usd** | **float** |  | 
**chain** | **str** |  | [optional] 
**paid_at** | **str** |  | [optional] 
**tier** | **str** |  | [optional] 

## Example

```python
from rugmunch.models.receipt_response import ReceiptResponse

# TODO update the JSON string below
json = "{}"
# create an instance of ReceiptResponse from a JSON string
receipt_response_instance = ReceiptResponse.from_json(json)
# print the JSON string representation of the object
print(ReceiptResponse.to_json())

# convert the object into a dict
receipt_response_dict = receipt_response_instance.to_dict()
# create an instance of ReceiptResponse from a dict
receipt_response_from_dict = ReceiptResponse.from_dict(receipt_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


