# ScanResult

Threat scan result.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**risk_score** | **int** |  | [optional] [default to 0]
**risk_level** | [**AppDomainTokenModelsRiskLevel**](AppDomainTokenModelsRiskLevel.md) |  | 
**flags** | [**List[ScanFlag]**](ScanFlag.md) |  | [optional] 
**modules_run** | **List[str]** |  | [optional] 
**scanned_at** | **datetime** |  | [optional] 

## Example

```python
from rugmunch.models.scan_result import ScanResult

# TODO update the JSON string below
json = "{}"
# create an instance of ScanResult from a JSON string
scan_result_instance = ScanResult.from_json(json)
# print the JSON string representation of the object
print(ScanResult.to_json())

# convert the object into a dict
scan_result_dict = scan_result_instance.to_dict()
# create an instance of ScanResult from a dict
scan_result_from_dict = ScanResult.from_dict(scan_result_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


