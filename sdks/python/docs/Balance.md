# Balance

Wallet balance + token holdings.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**native_balance** | **float** |  | [optional] [default to 0.0]
**native_symbol** | **str** |  | [optional] [default to '']
**total_value_usd** | **float** |  | [optional] [default to 0.0]
**tokens** | [**List[TokenHolding]**](TokenHolding.md) |  | [optional] 
**fetched_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.balance import Balance

# TODO update the JSON string below
json = "{}"
# create an instance of Balance from a JSON string
balance_instance = Balance.from_json(json)
# print the JSON string representation of the object
print(Balance.to_json())

# convert the object into a dict
balance_dict = balance_instance.to_dict()
# create an instance of Balance from a dict
balance_from_dict = Balance.from_dict(balance_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


