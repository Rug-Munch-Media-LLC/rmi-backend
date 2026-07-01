# TokenRisk

Token risk assessment.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**risk_score** | **int** |  | [optional] [default to 0]
**risk_level** | [**AppDomainTokenModelsRiskLevel**](AppDomainTokenModelsRiskLevel.md) |  | 
**flags** | **List[Dict[str, object]]** |  | [optional] 
**honeypot** | **bool** |  | [optional] [default to False]
**can_sell** | **bool** |  | [optional] [default to True]
**can_buy** | **bool** |  | [optional] [default to True]
**take_back** | **bool** |  | [optional] [default to False]
**owner_change_balance** | **bool** |  | [optional] [default to False]
**transfer_pausable** | **bool** |  | [optional] [default to False]
**analyzed_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.token_risk import TokenRisk

# TODO update the JSON string below
json = "{}"
# create an instance of TokenRisk from a JSON string
token_risk_instance = TokenRisk.from_json(json)
# print the JSON string representation of the object
print(TokenRisk.to_json())

# convert the object into a dict
token_risk_dict = token_risk_instance.to_dict()
# create an instance of TokenRisk from a dict
token_risk_from_dict = TokenRisk.from_dict(token_risk_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


