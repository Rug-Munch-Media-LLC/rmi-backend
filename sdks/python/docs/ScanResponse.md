# ScanResponse

Pydantic wrapper around the legacy ScanResult.  The legacy result is rich and complex (free/pro/elite per-tier data blocks, modules_run, confidence, etc.). We expose the most useful fields at the top level for API consumers.

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**address** | **str** |  | 
**chain** | **str** |  | 
**symbol** | **str** |  | [optional] [default to '']
**name** | **str** |  | [optional] [default to '']
**safety_score** | **int** |  | [optional] [default to 50]
**risk_flags** | **List[str]** |  | [optional] 
**tier_required** | **str** |  | [optional] [default to 'free']
**confidence** | **int** |  | [optional] [default to 0]
**modules_run** | [**List[ScanModuleResult]**](ScanModuleResult.md) |  | [optional] 
**free** | **Dict[str, object]** |  | [optional] 
**pro** | **Dict[str, object]** |  | [optional] 
**elite** | **Dict[str, object]** |  | [optional] 
**scanned_at** | **str** |  | [optional] [default to '']

## Example

```python
from rugmunch.models.scan_response import ScanResponse

# TODO update the JSON string below
json = "{}"
# create an instance of ScanResponse from a JSON string
scan_response_instance = ScanResponse.from_json(json)
# print the JSON string representation of the object
print(ScanResponse.to_json())

# convert the object into a dict
scan_response_dict = scan_response_instance.to_dict()
# create an instance of ScanResponse from a dict
scan_response_from_dict = ScanResponse.from_dict(scan_response_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


