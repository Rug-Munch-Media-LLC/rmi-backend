# WalletAnalysis

Full wallet analysis — risk + tokens + recent activity.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**truncated_address** | **str** |  | 
**risk_score** | **int** |  | [optional] [default to 0]
**risk_level** | [**AppDomainTokenModelsRiskLevel**](AppDomainTokenModelsRiskLevel.md) |  | 
**tokens** | [**List[TokenHolding]**](TokenHolding.md) |  | [optional] 
**recent_transactions** | [**List[Transaction]**](Transaction.md) |  | [optional] 
**labels** | **List[str]** | Known labels (e.g. &#39;Binance&#39;, &#39;Vitalik&#39;) | [optional] 
**flags** | [**List[ScanFlag]**](ScanFlag.md) |  | [optional] 
**total_value_usd** | **float** |  | [optional] [default to 0.0]
**analyzed_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.wallet_analysis import WalletAnalysis

# TODO update the JSON string below
json = "{}"
# create an instance of WalletAnalysis from a JSON string
wallet_analysis_instance = WalletAnalysis.from_json(json)
# print the JSON string representation of the object
print(WalletAnalysis.to_json())

# convert the object into a dict
wallet_analysis_dict = wallet_analysis_instance.to_dict()
# create an instance of WalletAnalysis from a dict
wallet_analysis_from_dict = WalletAnalysis.from_dict(wallet_analysis_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


