# TokenHolding

A single token holding within a wallet's portfolio.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**symbol** | **str** |  | 
**address** | **str** |  | [optional] [default to '']
**amount** | **float** |  | [optional] [default to 0.0]
**price_usd** | **float** |  | [optional] [default to 0.0]
**value_usd** | **float** |  | [optional] [default to 0.0]

## Example

```python
from rugmunch.models.token_holding import TokenHolding

# TODO update the JSON string below
json = "{}"
# create an instance of TokenHolding from a JSON string
token_holding_instance = TokenHolding.from_json(json)
# print the JSON string representation of the object
print(TokenHolding.to_json())

# convert the object into a dict
token_holding_dict = token_holding_instance.to_dict()
# create an instance of TokenHolding from a dict
token_holding_from_dict = TokenHolding.from_dict(token_holding_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


