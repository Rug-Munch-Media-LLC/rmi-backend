# TokenDetail

Full token details — metadata + supply + verification.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**name** | **str** |  | [optional] [default to '']
**symbol** | **str** |  | [optional] [default to '']
**decimals** | **int** |  | [optional] [default to 9]
**total_supply** | **float** |  | [optional] [default to 0.0]
**circulating_supply** | **float** |  | [optional] [default to 0.0]
**holders** | **int** |  | [optional] [default to 0]
**verified** | **bool** |  | [optional] [default to False]
**metadata** | **Dict[str, object]** |  | [optional] 
**fetched_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.token_detail import TokenDetail

# TODO update the JSON string below
json = "{}"
# create an instance of TokenDetail from a JSON string
token_detail_instance = TokenDetail.from_json(json)
# print the JSON string representation of the object
print(TokenDetail.to_json())

# convert the object into a dict
token_detail_dict = token_detail_instance.to_dict()
# create an instance of TokenDetail from a dict
token_detail_from_dict = TokenDetail.from_dict(token_detail_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


