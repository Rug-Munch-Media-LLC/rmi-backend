# TokenScanResult

Full token scan result.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**safety_score** | **float** |  | [optional] [default to 100.0]
**risk_score** | **int** |  | [optional] [default to 0]
**risk_level** | [**AppDomainTokenModelsRiskLevel**](AppDomainTokenModelsRiskLevel.md) |  | 
**risk_flags** | **List[str]** |  | [optional] 
**warnings** | **List[str]** |  | [optional] 
**modules_run** | **List[str]** |  | [optional] 
**metadata** | **Dict[str, object]** |  | [optional] 
**scanned_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.token_scan_result import TokenScanResult

# TODO update the JSON string below
json = "{}"
# create an instance of TokenScanResult from a JSON string
token_scan_result_instance = TokenScanResult.from_json(json)
# print the JSON string representation of the object
print(TokenScanResult.to_json())

# convert the object into a dict
token_scan_result_dict = token_scan_result_instance.to_dict()
# create an instance of TokenScanResult from a dict
token_scan_result_from_dict = TokenScanResult.from_dict(token_scan_result_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


